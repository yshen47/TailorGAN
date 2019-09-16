import os
import time
import torch
import numpy as np
import torchvision.utils as vutils
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from torch.utils.data import DataLoader
from util import util
from data import data_loader
from models import create_model
from options.options import Options
from tensorboardX import SummaryWriter

def plot_grad_flow(named_parameters):
    '''Plots the gradients flowing through different layers in the net during training.
    Can be used for checking for possible gradient vanishing / exploding problems.

    Usage: Plug this function in Trainer class after loss.backwards() as
    "plot_grad_flow(self.model.named_parameters())" to visualize the gradient flow'''
    ave_grads = []
    max_grads = []
    layers = []
    for n, p in named_parameters:
        if (p.requires_grad) and ("bias" not in n):
            layers.append(n)
            ave_grads.append(p.grad.abs().mean())
            max_grads.append(p.grad.abs().max())
    plt.bar(np.arange(len(max_grads)), max_grads, alpha=0.1, lw=1, color="c")
    plt.bar(np.arange(len(max_grads)), ave_grads, alpha=0.1, lw=1, color="b")
    plt.hlines(0, 0, len(ave_grads) + 1, lw=2, color="k")
    plt.xticks(range(0, len(ave_grads), 1), layers, rotation="vertical")
    plt.xlim(left=0, right=len(ave_grads))
    plt.ylim(bottom=-0.001, top=0.02)  # zoom in on the lower gradient regions
    plt.xlabel("Layers")
    plt.ylabel("average gradient")
    plt.title("Gradient flow")
    plt.grid(True)
    plt.legend([Line2D([0], [0], color="c", lw=4),
                Line2D([0], [0], color="b", lw=4),
                Line2D([0], [0], color="k", lw=4)], ['max-gradient', 'mean-gradient', 'zero-gradient'])
    plt.savefig("gradflow.jpg")
    plt.show()

opt = Options().parse()
print(opt)
start_epoch, epoch_iter = 1, 0

dataset = data_loader.InterDataset(opt)
loader = DataLoader(dataset, batch_size=opt.batch_size, num_workers=opt.num_workers, shuffle=True)
dataset_size = len(dataset)

model = create_model.create_model(opt)
model = model.cuda(opt.gpuid)
Tensor = torch.cuda.FloatTensor

total_steps = (opt.niter - start_epoch) * dataset_size + epoch_iter

display_delta = total_steps % opt.display_freq
print_delta = total_steps % opt.print_freq
save_delta = total_steps % opt.save_latest_freq

writer = SummaryWriter('./log_files/')

total_start_time = time.time()

for epoch in range(start_epoch, opt.niter+1):
    epoch_start_time = time.time()
    if epoch != start_epoch:
        epoch_iter = epoch_iter % dataset_size
    for i, data in enumerate(loader):
        if total_steps % opt.print_freq == print_delta:
            iter_start_time = time.time()
        total_steps += opt.batch_size
        epoch_iter += opt.batch_size

        edge_img, refer_edge_img, src_img, img_type, org_img = data

        edge_img = edge_img.cuda(opt.gpuid)
        refer_edge_img = refer_edge_img.cuda(opt.gpuid)
        src_img = src_img.cuda(opt.gpuid)
        img_type = img_type.cuda(opt.gpuid)
        org_img = org_img.cuda(opt.gpuid)

        # Reconstruction step
        # model.optimizer_edgeE.zero_grad()
        # model.optimizer_srcE.zero_grad()
        # model.optimizer_netG.zero_grad()
        refer_edge_feat = model.edgeE(refer_edge_img)
        src_feat = model.srcE(src_img)

        # Train discriminator
        for param in model.netD.parameters():
            param.requires_grad = True
        model.optimizer_netD.zero_grad()
        syn_feat = torch.cat((refer_edge_feat, src_feat), dim=1)
        syn_img = model.netG(syn_feat, src_img)
        org_img_d = model.netD(org_img)
        syn_img_d = model.netD(syn_img.detach())
        lossD_real = model.adv_loss(org_img_d, True, opt.gpuid)
        lossD_fake = model.adv_loss(syn_img_d, False, opt.gpuid)
        lossD = 0.5 * lossD_real + 0.5 * lossD_fake
        lossD.backward()
        model.optimizer_netD.step()

        # Synthesize step
        # model.optimizer_edgeE.zero_grad()
        # model.optimizer_srcE.zero_grad()
        for param in model.netD.parameters():
            param.requires_grad = False
        # model.optimizer_netD.zero_grad()
        model.optimizer_netG.zero_grad()
        syn_img = model.netG(syn_feat, src_img)
        syn_img_d = model.netD(syn_img)
        ganloss = model.adv_loss(syn_img_d, True, opt.gpuid)
        pred_class = model.classifier(syn_img)
        classloss = model.class_loss(pred_class, img_type)
        VGGloss = model.VGGloss(syn_img, org_img)
        loss = ganloss + classloss
        loss.backward()
        # plot_grad_flow(model.netG.named_parameters())
        model.optimizer_netG.step()

        # model.optimizer_srcE.step()
        # model.optimizer_edgeE.step()

        if total_steps % opt.print_freq == print_delta:
            total_time = (time.time() - total_start_time)
            epoch_time = (time.time() - epoch_start_time)
            iter_time = (time.time() - iter_start_time)
            print('epoch: %d/%d; iters: %d/%d; total_time: %.3f; epoch_time: %.3f; iter_time: %.3f'
                  % (epoch, opt.niter, (i+1)*opt.batch_size, dataset_size, total_time, epoch_time, iter_time))
            print('Total loss: %.5f; ganloss: %.5f; classloss: %.5f; VGGloss: %.5f; '
                  'discriminatorloss: %.5f'
                  % (loss.data, ganloss.data, classloss, VGGloss.data, lossD.data))
            # print('Total loss: %.5f; ganloss: %.5f; '
            #       'discriminatorloss: %.5f'
            #       % (loss.data, ganloss.data, lossD.data))

            # writer.add_scalar('Val/GANLoss', ganloss, epoch)
            # writer.add_scalar('Val/Classloss', classloss, epoch)
            # writer.add_scalar('Val/VGGloss', VGGloss, epoch)

        save_fake = total_steps % opt.display_freq == display_delta

        if save_fake:
            print('save imgs')
            print('')
            path = './result/syn_woE/' + str(epoch) + '/' + str((i + 1) * opt.batch_size)
            util.mkdir(path)
            vutils.save_image(
                org_img, '%s/org_imgs.png' % path,
                normalize=True
            )
            vutils.save_image(
                src_img, '%s/src_imgs.png' % path,
                normalize=True
            )
            vutils.save_image(
                refer_edge_img, '%s/refer_imgs.png' % path,
                normalize=True
            )
            vutils.save_image(
                syn_img.detach(), '%s/syn_imgs.png' % path,
                normalize=True
            )

    save_dir = opt.checkpoints_dir + '/TailorGAN_Garmentset/path/syn_woE/'
    util.mkdir(save_dir)

    if epoch % 10 == 0:
        # save_path_srcE = save_dir + 'TailorGAN_Garment_syn_srcE_%s.pth' % epoch
        # torch.save(model.srcE.state_dict(), save_path_srcE)
        # save_path_edgeE = save_dir + 'TailorGAN_Garment_syn_edgeE_%s.pth' % epoch
        # torch.save(model.edgeE.state_dict(), save_path_edgeE)
        save_path_netG = save_dir + 'TailorGAN_Garment_syn_netG_%s.pth' % epoch
        torch.save(model.netG.state_dict(), save_path_netG)
        save_path_netD = save_dir + 'TailorGAN_Garment_syn_netD_%s.pth' % epoch
        torch.save(model.netD.state_dict(), save_path_netD)
        print('Model saved!')

print('Training Finished')