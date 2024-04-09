import math

import torch
import torch.nn as nn

from .utils import to_cpu

# This new loss function is based on https://github.com/ultralytics/yolov3/blob/master/utils/loss.py


def bbox_iou(box1, box2, x1y1x2y2=True, GIoU=False, DIoU=False, CIoU=False, eps=1e-9):
    # Returns the IoU of box1 to box2. box1 is 4, box2 is nx4
    box2 = box2.T

    # Get the coordinates of bounding boxes
    if x1y1x2y2:  # x1, y1, x2, y2 = box1
        b1_x1, b1_y1, b1_x2, b1_y2 = box1[0], box1[1], box1[2], box1[3]
        b2_x1, b2_y1, b2_x2, b2_y2 = box2[0], box2[1], box2[2], box2[3]
    else:  # transform from xywh to xyxy
        b1_x1, b1_x2 = box1[0] - box1[2] / 2, box1[0] + box1[2] / 2
        b1_y1, b1_y2 = box1[1] - box1[3] / 2, box1[1] + box1[3] / 2
        b2_x1, b2_x2 = box2[0] - box2[2] / 2, box2[0] + box2[2] / 2
        b2_y1, b2_y2 = box2[1] - box2[3] / 2, box2[1] + box2[3] / 2

    # Intersection area
    inter = (torch.min(b1_x2, b2_x2) - torch.max(b1_x1, b2_x1)).clamp(0) * \
            (torch.min(b1_y2, b2_y2) - torch.max(b1_y1, b2_y1)).clamp(0)

    # Union Area
    w1, h1 = b1_x2 - b1_x1, b1_y2 - b1_y1 + eps
    w2, h2 = b2_x2 - b2_x1, b2_y2 - b2_y1 + eps
    union = w1 * h1 + w2 * h2 - inter + eps

    iou = inter / union
    if GIoU or DIoU or CIoU:
        # convex (smallest enclosing box) width
        cw = torch.max(b1_x2, b2_x2) - torch.min(b1_x1, b2_x1)
        ch = torch.max(b1_y2, b2_y2) - torch.min(b1_y1, b2_y1)  # convex height
        if CIoU or DIoU:  # Distance or Complete IoU https://arxiv.org/abs/1911.08287v1
            c2 = cw ** 2 + ch ** 2 + eps  # convex diagonal squared
            rho2 = ((b2_x1 + b2_x2 - b1_x1 - b1_x2) ** 2 +
                    (b2_y1 + b2_y2 - b1_y1 - b1_y2) ** 2) / 4  # center distance squared
            if DIoU:
                return iou - rho2 / c2  # DIoU
            elif CIoU:  # https://github.com/Zzh-tju/DIoU-SSD-pytorch/blob/master/utils/box/box_utils.py#L47
                v = (4 / math.pi ** 2) * \
                    torch.pow(torch.atan(w2 / h2) - torch.atan(w1 / h1), 2)
                with torch.no_grad():
                    alpha = v / ((1 + eps) - iou + v)
                return iou - (rho2 / c2 + v * alpha)  # CIoU
        else:  # GIoU https://arxiv.org/pdf/1902.09630.pdf
            c_area = cw * ch + eps  # convex area
            return iou - (c_area - union) / c_area  # GIoU
    else:
        return iou  # IoU

def yolo_loss(combined_predictions, yolo_targets, model):
    yolo_predictions, seg_predictions = combined_predictions
    device = yolo_targets.device

    lcls, lbox, lobj = torch.zeros(1, device=device), torch.zeros(1, device=device), torch.zeros(1, device=device)
    tcls, tbox, indices, anchors = build_targets(yolo_predictions, yolo_targets, model)

    BCEcls = nn.BCEWithLogitsLoss(
        pos_weight=torch.tensor([1.0], device=device),reduce=False)
    BCEobj = nn.BCEWithLogitsLoss(
        pos_weight=torch.tensor([1.0], device=device),reduce=False)
    # Calculate losses for each yolo layer
    for layer_index, layer_predictions in enumerate(yolo_predictions):
        b, anchor, grid_j, grid_i = indices[layer_index]
        tobj = torch.zeros_like(layer_predictions[..., 0], device=device)
        num_targets = b.shape[0]
        if num_targets:
            ps = layer_predictions[b, anchor, grid_j, grid_i]
            pxy = ps[:, :2].sigmoid()
            pwh = torch.exp(ps[:, 2:4]) * anchors[layer_index]
            pbox = torch.cat((pxy, pwh), 1)
            iou = bbox_iou(pbox.T, tbox[layer_index], x1y1x2y2=False, CIoU=True)
            lbox += (1.0 - iou).mean()  # iou loss
            tobj[b, anchor, grid_j, grid_i] = iou.detach().clamp(0).type(tobj.dtype)
            if ps.size(1) - 5 > 1:
                t = torch.zeros_like(ps[:, 5:], device=device)  # targets
                t[range(num_targets), tcls[layer_index]] = 1
                lcls += BCEcls(ps[:, 5:], t)  # BCE
        lobj += BCEobj(layer_predictions[..., 4], tobj) # obj loss
    
    # Scalaing of losses
    lbox *= 0.2
    lobj *= 10.0
    lcls *= 0.05

    loss = lbox + lobj + lcls
    return loss, to_cpu(torch.cat((lbox, lobj, lcls, loss)))

def unet_loss(combined_predictions, seg_targets, model):
    yolo_predictions, seg_predictions = combined_predictions
    device = seg_targets.device
    seg_loss = nn.CrossEntropyLoss()(seg_predictions[0], seg_targets).unsqueeze(0)
    return seg_loss


def compute_loss(combined_predictions, combined_targets, model):
    # Split seg and yolo stuff
    yolo_targets, seg_targets, haveboxes,havemasks = combined_targets
    yolo_predictions, seg_predictions = combined_predictions    # Check which device was used
    # Check which device was used
    device = yolo_targets.device

    """
    print("****   Loss   ****")
    print(len(seg_predictions))
    print(seg_predictions[0].shape)
    print(seg_targets.shape)
    print(len(yolo_predictions))
    print(yolo_predictions[0].shape)
    print(yolo_predictions[1].shape)
    print(yolo_targets.shape)
    print(haveboxes.shape)
    print(havemasks.shape)
    """

    seg_loss = torch.zeros(1, device=device)
    # Segmentation loss
    cel = nn.CrossEntropyLoss(reduce=False)(seg_predictions[0], seg_targets).unsqueeze(0)
    print(f"Seg loss {cel.shape}")
    celsum = torch.sum(cel,(2,3))
    print(f"Seg loss {celsum.shape}")
    for i,loss in enumerate(celsum[0]):
        print(loss,havemasks[i])
        if havemasks[i]:
            seg_loss += loss
    print(f"Loss: {seg_loss[0]}")

    # Detection loss
    # Add placeholder varables for the different losses
    lcls, lbox, lobj = torch.zeros(1, device=device), torch.zeros(1, device=device), torch.zeros(1, device=device)

    # Build yolo targets
    tcls, tbox, indices, anchors = build_targets(yolo_predictions, yolo_targets, model)  # targets

    # Define different loss functions classification
    BCEcls = nn.BCEWithLogitsLoss(
        pos_weight=torch.tensor([1.0], device=device),reduce=False)
    BCEobj = nn.BCEWithLogitsLoss(
        pos_weight=torch.tensor([1.0], device=device),reduce=False)

    
    # Calculate losses for each yolo layer
    for layer_index, layer_predictions in enumerate(yolo_predictions):
        # Get image ids, anchors, grid index i and j for each target in the current yolo layer
        b, anchor, grid_j, grid_i = indices[layer_index]
        # Build empty object target tensor with the same shape as the object prediction
        tobj = torch.zeros_like(layer_predictions[..., 0], device=device)  # target obj
        # Get the number of targets for this layer.
        # Each target is a label box with some scaling and the association of an anchor box.
        # Label boxes may be associated to 0 or multiple anchors. So they are multiple times or not at all in the targets.
        num_targets = b.shape[0]
        # Check if there are targets for this batch
        if num_targets:
            # Load the corresponding values from the predictions for each of the targets
            ps = layer_predictions[b, anchor, grid_j, grid_i]

            # Regression of the box
            # Apply sigmoid to xy offset predictions in each cell that has a target
            pxy = ps[:, :2].sigmoid()
            # Apply exponent to wh predictions and multiply with the anchor box that matched best with the label for each cell that has a target
            pwh = torch.exp(ps[:, 2:4]) * anchors[layer_index]
            # Build box out of xy and wh
            pbox = torch.cat((pxy, pwh), 1)
            # Calculate CIoU or GIoU for each target with the predicted box for its cell + anchor
            iou = bbox_iou(pbox.T, tbox[layer_index], x1y1x2y2=False, CIoU=True)
            # We want to minimize our loss so we and the best possible IoU is 1 so we take 1 - IoU and reduce it with a mean
            lbox += (1.0 - iou).mean()  # iou loss

            # Classification of the objectness
            # Fill our empty object target tensor with the IoU we just calculated for each target at the targets position
            tobj[b, anchor, grid_j, grid_i] = iou.detach().clamp(0).type(tobj.dtype)  # Use cells with iou > 0 as object targets

            # Classification of the class
            # Check if we need to do a classification (number of classes > 1)
            if ps.size(1) - 5 > 1:
                # Hot one class encoding
                t = torch.zeros_like(ps[:, 5:], device=device)  # targets
                t[range(num_targets), tcls[layer_index]] = 1
                # Use the tensor to calculate the BCE loss
                #lcls += BCEcls(ps[:, 5:], t)  # BCE
                bcec = BCEcls(ps[:, 5:], t)
                print(f"Class BCE {bcec.shape}")
                for i,lossval in enumerate(bcec):
                    if haveboxes[i]:
                        lbox += lossval

        # Classification of the objectness the sequel
        # Calculate the BCE loss between the on the fly generated target and the network prediction
        bceo = BCEobj(layer_predictions[..., 4], tobj) # obj loss
        print(f"Object BCE {bceo.shape}")
        for i,lossval in enumerate(bceo):
            if haveboxes[i]:
                lbox += lossval
    
    # Scalaing of losses
    lbox *= 0.2
    lobj *= 10.0
    lcls *= 0.05

    # Merge losses
    loss = lbox + lobj + lcls + seg_loss

    return loss, to_cpu(torch.cat((lbox, lobj, lcls, seg_loss, loss)))


def build_targets(p, targets, model):
    # Build targets for compute_loss(), input targets(image,class,x,y,w,h)
    na, nt = len(model.yolo_layers[0].anchors), targets.shape[0]  # number of anchors, targets
    tcls, tbox, indices, anch = [], [], [], []
    gain = torch.ones(7, device=targets.device)  # normalized to gridspace gain
    # Make a tensor that iterates 0-2 for 3 anchors and repeat that as many times as we have target boxes
    ai = torch.arange(na, device=targets.device).float().view(na, 1).repeat(1, nt)
    # Copy target boxes anchor size times and append an anchor index to each copy the anchor index is also expressed by the new first dimension
    targets = torch.cat((targets.repeat(na, 1, 1), ai[:, :, None]), 2)

    for i, yolo_layer in enumerate(model.yolo_layers):
        # Scale anchors by the yolo grid cell size so that an anchor with the size of the cell would result in 1
        anchors = yolo_layer.anchors / yolo_layer.stride
        # Add the number of yolo cells in this layer the gain tensor
        # The gain tensor matches the collums of our targets (img id, class, x, y, w, h, anchor id)
        gain[2:6] = torch.tensor(p[i].shape)[[3, 2, 3, 2]]  # xyxy gain
        # Scale targets by the number of yolo layer cells, they are now in the yolo cell coordinate system
        t = targets * gain
        # Check if we have targets
        if nt:
            t = t.squeeze(0)
            # Check if we use anchor boxes
            if na != 1:
                # Calculate ratio between anchor and target box for both width and height
                r = t[:, :, 4:6] / anchors[:, None]
                # Select the ratios that have the highest divergence in any axis and check if the ratio is less than 4
                j = torch.max(r, 1. / r).max(2)[0] < 4
                # Only use targets that have the correct ratios for their anchors
                # That means we only keep ones that have a matching anchor and we loose the anchor dimension
                # The anchor id is still saved in the 7th value of each target
                t = t[j]
        else:
            t = targets[0]

        # Extract image id in batch and class id
        b, c = t[:, :2].long().T
        # We isolate the target cell associations.
        # x, y, w, h are allready in the cell coordinate system meaning an x = 1.2 would be 1.2 times cellwidth
        gxy = t[:, 2:4]
        gwh = t[:, 4:6]  # grid wh
        # Cast to int to get an cell index e.g. 1.2 gets associated to cell 1
        gij = gxy.long()
        # Isolate x and y index dimensions
        gi, gj = gij.T  # grid xy indices

        # Convert anchor indexes to int
        a = t[:, 6].long()
        # Add target tensors for this yolo layer to the output lists
        # Add to index list and limit index range to prevent out of bounds
        indices.append((b, a, gj.clamp_(0, gain[3].long() - 1), gi.clamp_(0, gain[2].long() - 1)))
        # Add to target box list and convert box coordinates from global grid coordinates to local offsets in the grid cell
        tbox.append(torch.cat((gxy - gij, gwh), 1))  # box
        # Add correct anchor for each target to the list
        anch.append(anchors[a])
        # Add class for each target to the list
        tcls.append(c)

    return tcls, tbox, indices, anch
