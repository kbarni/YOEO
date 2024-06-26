#! /usr/bin/env python3

from __future__ import division, annotations

import os
import argparse
import tqdm

import numpy as np

import torch
from torch.utils.data import DataLoader
import torch.optim as optim
from torch.autograd import Variable

from yoeo.models import load_model
from yoeo.utils.logger import Logger
from yoeo.utils.utils import to_cpu, print_environment_info, provide_determinism, worker_seed_set
from yoeo.utils.datasets import ListDataset
from yoeo.utils.dataclasses import ClassNames
from yoeo.utils.class_config import ClassConfig
from yoeo.utils.augmentations import AUGMENTATION_TRANSFORMS
from yoeo.utils.transforms import DEFAULT_TRANSFORMS
from yoeo.utils.parse_config import parse_data_config
from yoeo.utils.loss import compute_loss,unet_loss,yolo_loss
from yoeo.test import _evaluate, _create_validation_data_loader

from terminaltables import AsciiTable

from torchsummary import summary


def _create_data_loader(img_path, batch_size, img_size, n_cpu, multiscale_training=False,is_detect=False,is_segment=False):
    """Creates a DataLoader for training.

    :param img_path: Path to file containing all paths to training images.
    :type img_path: str
    :param batch_size: Size of each image batch
    :type batch_size: int
    :param img_size: Size of each image dimension for yolo
    :type img_size: int
    :param n_cpu: Number of cpu threads to use during batch generation
    :type n_cpu: int
    :param multiscale_training: Scale images to different sizes randomly
    :type multiscale_training: bool
    :return: Returns DataLoader
    :rtype: DataLoader
    """
    dataset = ListDataset(
        img_path,
        img_size=img_size,
        multiscale=multiscale_training,
        transform=AUGMENTATION_TRANSFORMS,is_detect=is_detect,is_segment=is_segment)
    dataloader = DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=True,
        num_workers=n_cpu,
        pin_memory=True,
        collate_fn=dataset.collate_fn,
        worker_init_fn=worker_seed_set)
    return dataloader


def run():
    print_environment_info()
    parser = argparse.ArgumentParser(description="Trains the YOEO model.")
    parser.add_argument("-m", "--model", type=str, default="config/yoeo.cfg", help="Path to model definition file (.cfg)")
    parser.add_argument("-d", "--data", type=str, default="config/torso.data", help="Path to data config file (.data)")
    parser.add_argument("-e", "--epochs", type=int, default=300, help="Number of epochs")
    parser.add_argument("-v", "--verbose", action='store_true', help="Makes the training more verbose")
    parser.add_argument("--n_cpu", type=int, default=8, help="Number of cpu threads to use during batch generation")
    parser.add_argument("--use_cpu", action='store_true', help="Force using CPU instead of GPU")
    parser.add_argument("--pretrained_weights", type=str, help="Path to checkpoint file (.weights or .pth). Starts training from checkpoint model")
    parser.add_argument("--checkpoint_interval", type=int, default=1, help="Interval of epochs between saving model weights")
    parser.add_argument("--checkpoint_dir", type=str, default="checkpoints", help="Directory in which the checkpoints are stored")
    parser.add_argument("--evaluation_interval", type=int, default=1, help="Interval of epochs between evaluations on validation set")
    parser.add_argument("--multiscale_training", action="store_true", help="Allow multi-scale training")
    parser.add_argument("--iou_thres", type=float, default=0.5, help="Evaluation: IOU threshold required to qualify as detected")
    parser.add_argument("--conf_thres", type=float, default=0.1, help="Evaluation: Object confidence threshold")
    parser.add_argument("--nms_thres", type=float, default=0.5, help="Evaluation: IOU threshold for non-maximum suppression")
    parser.add_argument("--logdir", type=str, default="logs", help="Directory for training log files (e.g. for TensorBoard)")
    parser.add_argument("--seed", type=int, default=-1, help="Makes results reproducable. Set -1 to disable.")
    parser.add_argument("--class_config", type=str, default="class_config/default.yaml", help="Class configuration for evaluation")
    args = parser.parse_args()
    print(f"Command line arguments: {args}")

    if args.seed != -1:
        provide_determinism(args.seed)

    logger = Logger(args.logdir)  # Tensorboard logger

    # Create output directories if missing
    os.makedirs("output", exist_ok=True)
    os.makedirs(args.checkpoint_dir, exist_ok=True)

    # Get data configuration
    data_config = parse_data_config(args.data)
    unettrain_path = data_config["unettrain"]
    unetvalid_path = data_config["unetvalid"]
    yolotrain_path = data_config["yolotrain"]
    yolovalid_path = data_config["yolovalid"]

    class_names = ClassNames.load_from(data_config["names"])
    class_config = ClassConfig.load_from(args.class_config, class_names)

    if args.use_cpu:
        device = "cpu"
    else:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # ############
    # Create model
    # ############

    model = load_model(args.model, args.pretrained_weights,device=device)

    # Print model
    if args.verbose:
        summary(model, input_size=(3, model.hyperparams['height'], model.hyperparams['height']))

    mini_batch_size = model.hyperparams['batch'] // model.hyperparams['subdivisions']

    # #################
    # Create Dataloader
    # #################

    # Load training dataloader
    yolodataloader = _create_data_loader(
        yolotrain_path,
        mini_batch_size,
        model.hyperparams['height'],
        args.n_cpu,
        args.multiscale_training,is_detect=True)

    # Load validation dataloader
    yolovalidation_dataloader = _create_validation_data_loader(
        yolovalid_path,
        mini_batch_size,
        model.hyperparams['height'],
        args.n_cpu,is_detect=True)

    # Load training dataloader
    unetdataloader = _create_data_loader(
        unettrain_path,
        mini_batch_size,
        model.hyperparams['height'],
        args.n_cpu,
        args.multiscale_training,is_segment=True)

    # Load validation dataloader
    unetvalidation_dataloader = _create_validation_data_loader(
        unetvalid_path,
        mini_batch_size,
        model.hyperparams['height'],
        args.n_cpu,is_segment=True)
    """
    # ###########
    # Check image
    # ###########
    import cv2
    cv2.namedWindow("Image",cv2.WINDOW_NORMAL)
    for i in range(1):
        (path, imgs, bb_targets, mask_targets) = next(iter(yolodataloader))
        print(path[0])
        print(imgs[0].shape)
        img = imgs[0].numpy().transpose(1, 2, 0).copy() #cv2.imread(path[0])
        print(img.shape)
        for box in bb_targets:
            print(box)
            w=img.shape[1]
            h=img.shape[0]
            x0=int((box[2]-box[4]/2)*w)
            x1=int((box[2]+box[4]/2)*w)
            y0=int((box[3]-box[5]/2)*h)
            y1=int((box[3]+box[5]/2)*h)
            print(f"{x0} {y0} {x1} {y1}")
            cv2.rectangle(img,(x0,y0),(x1,y1),(1,0,0),2)
        cv2.imshow("Image",img)
        cv2.waitKey()
    exit()
    """
    # ################
    # Create optimizer
    # ################

    params = [p for p in model.parameters() if p.requires_grad]

    if (model.hyperparams['optimizer'] in [None, "adam"]):
        optimizer = optim.Adam(
            params,
            lr=model.hyperparams['learning_rate'],
            weight_decay=model.hyperparams['decay'],
        )
    elif (model.hyperparams['optimizer'] == "sgd"):
        optimizer = optim.SGD(
            params,
            lr=model.hyperparams['learning_rate'],
            weight_decay=model.hyperparams['decay'],
            momentum=model.hyperparams['momentum'])
    else:
        print("Unknown optimizer. Please choose between (adam, sgd).")

    # skip epoch zero, because then the calculations for when to evaluate/checkpoint makes more intuitive sense
    # e.g. when you stop after 30 epochs and evaluate every 10 epochs then the evaluations happen after: 10,20,30
    # instead of: 0, 10, 20
    batches_done=0

    #print(model)

    for epoch in range(1, args.epochs+1):

        print("\n---- Training Model ----")
        seg_loss = iou_loss = obj_loss = cls_loss = total_loss = 0
        model.train()  # Set model to training mode

        for batch_i, (_, imgs, bb_targets, mask_targets) in enumerate(tqdm.tqdm(unetdataloader, desc=f"Training Epoch {epoch} / SEGM")):
            batches_done += 1

            imgs = Variable(imgs.to(device, non_blocking=True))
            mask_targets = Variable(mask_targets.to(device=device), requires_grad=False)

            outputs = model(imgs)

            loss = unet_loss(outputs, mask_targets, model)
            seg_loss += to_cpu(loss).item()
            total_loss += to_cpu(loss).item()

            loss.backward()

            ###############
            # Run optimizer
            ###############

            if batches_done % model.hyperparams['subdivisions'] == 0:
                # Adapt learning rate
                # Get learning rate defined in cfg
                lr = model.hyperparams['learning_rate']
                if batches_done < model.hyperparams['burn_in']:
                    # Burn in
                    lr *= (batches_done / model.hyperparams['burn_in'])
                else:
                    # Set and parse the learning rate to the steps defined in the cfg
                    for threshold, value in model.hyperparams['lr_steps']:
                        if batches_done > threshold:
                            lr *= value
                # Log the learning rate
                logger.scalar_summary("train/learning_rate", lr, batches_done)
                # Set learning rate
                for g in optimizer.param_groups:
                    g['lr'] = lr

                # Run optimizer
                optimizer.step()
                # Reset gradients
                optimizer.zero_grad()

            model.seen += imgs.size(0)

        for batch_i, (_, imgs, bb_targets, mask_targets) in enumerate(tqdm.tqdm(yolodataloader, desc=f"Training Epoch {epoch} / YOLO")):
            batches_done += 1

            imgs = Variable(imgs.to(device, non_blocking=True))
            bb_targets = Variable(bb_targets.to(device=device), requires_grad=False)

            outputs = model(imgs)

            loss,loss_detail = yolo_loss(outputs, bb_targets, model)
            iou_loss += float(loss_detail[0])
            obj_loss += float(loss_detail[1])
            cls_loss += float(loss_detail[2])
            total_loss += to_cpu(loss).item()
            loss.backward()

            ###############
            # Run optimizer
            ###############

            if batches_done % model.hyperparams['subdivisions'] == 0:
                # Adapt learning rate
                # Get learning rate defined in cfg
                lr = model.hyperparams['learning_rate']
                if batches_done < model.hyperparams['burn_in']:
                    # Burn in
                    lr *= (batches_done / model.hyperparams['burn_in'])
                else:
                    # Set and parse the learning rate to the steps defined in the cfg
                    for threshold, value in model.hyperparams['lr_steps']:
                        if batches_done > threshold:
                            lr *= value
                # Log the learning rate
                logger.scalar_summary("train/learning_rate", lr, batches_done)
                # Set learning rate
                for g in optimizer.param_groups:
                    g['lr'] = lr

                # Run optimizer
                optimizer.step()
                # Reset gradients
                optimizer.zero_grad()

            model.seen += imgs.size(0)

        seg_loss /= unetdataloader.size()
        iou_loss /= yolodataloader.size()
        cls_loss /= yolodataloader.size()
        obj_loss /= yolodataloader.size()
        
        # ############
        # Log progress
        # ############
        if args.verbose:
            print(AsciiTable(
                [
                    ["Type", "Value"],
                    ["IoU loss", iou_loss],
                    ["Object loss", obj_loss],
                    ["Class loss", cls_loss],
                    ["Segmentation loss", seg_loss],
                    ["Epoch loss", to_cpu(loss).item()],
                ]).table)
        else:
            print(f'Epoch loss: {total_loss}')
        """
        # Tensorboard logging
        tensorboard_log = [
            ("train/iou_loss", float(loss_components[0])),
            ("train/obj_loss", float(loss_components[1])),
            ("train/class_loss", float(loss_components[2])),
            ("train/seg_loss", float(loss_components[3])),
            ("train/loss", to_cpu(loss).item())]
        logger.list_of_scalars_summary(tensorboard_log, batches_done)
        """

        # #############
        # Save progress
        # #############

        # Save model to checkpoint file
        if epoch % args.checkpoint_interval == 0:
            checkpoint_path = os.path.join(args.checkpoint_dir, f"yoeo_checkpoint_{epoch}.pth")
            print(f"---- Saving checkpoint to: '{checkpoint_path}' ----")
            torch.save(model.state_dict(), checkpoint_path)

        # ########
        # Evaluate
        # ########
        """
        if epoch % args.evaluation_interval == 0:
            print("\n---- Evaluating Model ----")
            # Evaluate the model on the validation set
            metrics_output = _evaluate(
                model,
                yolovalidation_dataloader,
                class_config=class_config,
                img_size=model.hyperparams['height'],
                iou_thres=args.iou_thres,
                conf_thres=args.conf_thres,
                nms_thres=args.nms_thres,
                verbose=args.verbose,
            )

            if metrics_output is not None:
                precision, recall, AP, f1, ap_class = metrics_output[0]
                seg_class_ious = metrics_output[1]
                evaluation_metrics = [
                    ("validation/precision", precision.mean()),
                    ("validation/recall", recall.mean()),
                    ("validation/mAP", AP.mean()),
                    ("validation/f1", f1.mean()),
                    ("validation/seg_iou", np.array(seg_class_ious).mean())]
                
                if metrics_output[2] is not None:
                    evaluation_metrics.append(("validation/secondary_mbACC", metrics_output[2].mbACC()))

                logger.list_of_scalars_summary(evaluation_metrics, epoch)
        """

    print(f"****  Training finished   ****\n---- Saving network")
    torch.save(model.state_dict,"weights/pfd_network_sd.pth")
    torch.save(model,"weights/pfd_network.pth")

if __name__ == "__main__":
    run()
