####################################################################################
# HLD BUILDING BLOCK: TRAINING                                                     #
####################################################################################
# Run the test.
# Compute the metrics (e.g. accuracy) obtained.
####################################################################################

import argparse
import os
import random
import torch
import torch.nn.parallel
import torch.optim as optim
import torch.utils.data
from B0_Dataset.dataset import SemanticKittiDataset
from D0_Modeling.model import SegmentationPointNet
from torch.utils.data import DataLoader
# from A0_Configuration.hyperparam import opt
import torch.nn.functional as F
from tqdm import tqdm
import numpy as np

from torch.utils.tensorboard import SummaryWriter
writer = SummaryWriter('F1_Documentation/runs/Pointnet_experiment_0')

def train(opt):
    # device = "cuda"
    blue = lambda x: '\033[94m' + x + '\033[0m'

    torch.manual_seed(123)


    train_dataset = SemanticKittiDataset(
        dst_hparamDatasetPath=opt.hparamDatasetPath,
        dst_hparamDatasetSequence=opt.hparamDatasetSequence,
        dst_hparamYamlConfigPath=opt.hparamYamlConfigPath,
        dst_hparamNumberOfRandomPoints=opt.hparamNumPoints,
        dst_hparamActionType='train',
        dst_hparamPointDimension=4)
    # y = next(iter(train_dataset))
    # print(len(y))


    val_dataset = SemanticKittiDataset(
        dst_hparamDatasetPath=opt.hparamDatasetPath,
        dst_hparamDatasetSequence=opt.hparamValDatasetSequence,
        dst_hparamYamlConfigPath=opt.hparamYamlConfigPath,
        dst_hparamNumberOfRandomPoints=opt.hparamNumPoints,
        dst_hparamActionType='val',
        dst_hparamPointDimension=4)



    train_dataloader = DataLoader(
        dataset = train_dataset,
        batch_size=opt.hparamTrainBatchSize,
        shuffle=True)

    val_dataloader = DataLoader(
        dataset = val_dataset,
        batch_size=opt.hparamValBatchSize, #hparamTrainBatchSize
        shuffle=True)

    

    print(len(train_dataset), len(val_dataset))
    print('classes', opt.hparamNumberOfClasses)



    lr = opt.hparamOptimizerLearningRate
    num_classes=opt.hparamNumberOfClasses
    feature_transform=opt.hparamFeatureTransform
    model = SegmentationPointNet(num_classes, feature_transform)


    torch.cuda.is_available()
    optimizer = optim.Adam(model.parameters(), lr=lr, betas=(0.9, 0.999))
    scheduler = optim.lr_scheduler.StepLR(optimizer, step_size=20, gamma=0.5)
    model = model.to(opt.hparamDeviceType)


    num_batch = len(train_dataset) / opt.hparamTrainBatchSize

    best_loss = 1.3
    for epoch in range(opt.hparamNumberOfEpochs):
        scheduler.step()
        for i, data in enumerate(train_dataloader, 0):
            points, target = data
            points = points.transpose(2, 1)
            points, target = points.to(opt.hparamDeviceType), target.to(opt.hparamDeviceType)
            optimizer.zero_grad()
            model = model.train()
            pred,trans_feat = model(points) # pred.shape=torch.Size([32, 4000, 20]) & target.shape = torch.Size([32, 4000])
            pred = pred.view(-1, num_classes) 
            target = target.view(-1, 1)[:, 0]
            loss = F.nll_loss(pred, target) #Consecutive_Predictions(Target) pred=[4000*32, 20])
            if opt.hparamFeatureTransform:
                loss += feature_transform(trans_feat) * 0.001
            loss.backward()
            optimizer.step()
            pred_choice = pred.data.max(1)[1]
            correct = pred_choice.eq(target.data).cpu().sum()
            print('[%d: %d/%d] train loss: %f accuracy: %f' % (epoch, i, num_batch, loss.item(),correct.item()/float(opt.hparamTrainBatchSize*opt.hparamNumPoints)))
            writer.add_scalar('Training Loss', loss.item(), epoch*len(train_dataloader) + i)
            writer.add_scalar('Training Accuracy',  correct.item()/float(opt.hparamTrainBatchSize*opt.hparamNumPoints),epoch*len(train_dataloader) + i)
            

            if i % 10 == 0:
                j, data = next(enumerate(val_dataloader, 0))
                points, target = data
                points = points.transpose(2, 1)
                points, target = points.to(opt.hparamDeviceType), target.to(opt.hparamDeviceType)
                model = model.eval()
                pred,_ = model(points)
                pred = pred.view(-1, num_classes)
                target = target.view(-1, 1)[:, 0]
                loss = F.nll_loss(pred, target)
                pred_choice = pred.data.max(1)[1]
                correct = pred_choice.eq(target.data).cpu().sum()
                print('[%d: %d/%d] %s loss: %f accuracy: %f' % (epoch, i, num_batch, blue('val'), loss.item(), correct.item()/float(opt.hparamTrainBatchSize * opt.hparamNumPoints)))
                
                # TensorBoard
                writer.add_scalar('Validation Loss', loss.item(), epoch*len(val_dataloader) + i)
                writer.add_scalar('Validation Accuracy',  correct.item()/float(opt.hparamTrainBatchSize*opt.hparamNumPoints),epoch*len(val_dataloader) + i)
                
                # Saving the model
                actual_accuracy =  correct.item()/float(opt.hparamTrainBatchSize*opt.hparamNumPoints)       
                actual_loss = loss.item()

                if actual_loss < best_loss:
                        best_loss = actual_loss
                        torch.save(model.state_dict(), '%s/seg_model_%d.pth' % (opt.hparamOutputFolder, epoch))
                        print(f"The Model has been saved.(Model_saved/.PTH)")

        

    ## benchmark mIOU
    shape_ious = []
    for i,data in tqdm(enumerate(val_dataloader, 0)):
        points, target = data
        points = points.transpose(2, 1)
        points, target = points.to(opt.hparamDeviceType), target.to(opt.hparamDeviceType)
        model = model.eval()
        pred, _,= model(points)
        pred_choice = pred.data.max(2)[1]

        pred_np = pred_choice.cpu().data.numpy()
        target_np = target.cpu().data.numpy() - 1

        for shape_idx in range(target_np.shape[0]):
            parts = range(num_classes)#np.unique(target_np[shape_idx])
            part_ious = []
            for part in parts:
                I = np.sum(np.logical_and(pred_np[shape_idx] == part, target_np[shape_idx] == part))
                U = np.sum(np.logical_or(pred_np[shape_idx] == part, target_np[shape_idx] == part))
                if U == 0:
                    iou = 1 #If the union of groundtruth and prediction points is empty, then count part IoU as 1
                else:
                    iou = I / float(U)
                part_ious.append(iou)
            shape_ious.append(np.mean(part_ious))

    print("mIOU: {}".format(np.mean(shape_ious)))
    writer.add_scalar(np.mean(shape_ious))
            
