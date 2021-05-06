import torch
import hawp.parsing
from hawp.parsing.config import cfg
from hawp.parsing.utils.comm import to_device
from hawp.parsing.dataset.build import build_transform
from hawp.parsing.detector import WireframeDetector
from hawp.parsing.utils.logger import setup_logger
from hawp.parsing.utils.metric_logger import MetricLogger
from hawp.parsing.utils.miscellaneous import save_config
from hawp.parsing.utils.checkpoint import DetectronCheckpointer
from skimage import io
import os
import os.path as osp
import time
import datetime
import argparse
import logging
import matplotlib.pyplot as plt
from tqdm import tqdm
import json

import numpy as np
import cv2
import sys
import random

from lines import tennis_court_model_points

def argument_parsing():
    parser = argparse.ArgumentParser(description='HAWP Testing')

    parser.add_argument("--config-file",
                        metavar="FILE",
                        help="path to config file",
                        type=str,
                        required=True,
                        )

    parser.add_argument("--img",default="",type=str,required=False,
                        help="image path")

    parser.add_argument("--img_directory",default="",type=str,required=False,
                        help="input images directory")
    parser.add_argument("--output_path",type=str,required=False,
                        help="output path, img not show if specified")

    parser.add_argument("--threshold",
                        type=float,
                        default=0.97)
    
    return parser.parse_args()

def get_lines_from_nn(cfg, impath, image, model, device, threshold):
    transform = build_transform(cfg)
    image_tensor = transform(image.astype(float))[None].to(device)
    meta = {
        'filename': impath,
        'height': image.shape[0],
        'width': image.shape[1],
    }

    with torch.no_grad():
        output, _ = model(image_tensor,[meta])
        output = to_device(output,'cpu')
    
    lines = output['lines_pred'].numpy()
    scores = output['lines_score'].numpy() # possible use for matching priority
    idx = scores>threshold

    return lines[idx]

def test_single_image(cfg, impath, model, device, output_path = "", threshold = 0.97):
    image = cv2.imread(impath)
    lines = get_lines_from_nn(cfg, impath, image[:, :, [2, 1, 0]], model, device, threshold)

    img_with_lines = np.copy(image)
    for line in lines:
        line = line.astype(np.int32)
        img_with_lines = cv2.line(img_with_lines, (line[0], line[1]), (line[2], line[3]), (255, 0, 0), 2)
        img_with_lines = cv2.circle(img_with_lines, (line[0], line[1]), 2, (255, 80, 0), 3)
        img_with_lines = cv2.circle(img_with_lines, (line[2], line[3]), 2, (255, 80, 0), 3)
    cv2.imshow('img_with_lines', img_with_lines)
    cv2.waitKey(0)
    
    # points = np.float32(np.asarray([np.append(lines[:,0], lines[:, 2]), np.append(lines[:, 1], lines[:, 3])]).T)
    points = np.asarray([np.append(lines[:,0], lines[:,2]),np.append(lines[:,1], lines[:,3])]).T
    print(points.shape)
    print(tennis_court_model_points.shape)

    img_with_points = np.copy(image)
    for point in points:
        point = point.astype(np.int32)
        img_with_points = cv2.circle(img_with_points, (point[0], point[1]), 2, (255, 80, 0), 3)
    cv2.imshow('img_with_points', img_with_points)
    cv2.waitKey(0)

    tennis_court_model_points_reshaped = np.float32(tennis_court_model_points[:, np.newaxis, :])
    points_reshaped = np.float32(points[:, np.newaxis, :])

    """
    MIN_MATCH_COUNT = 10
    # Initiate SIFT detector
    sift = cv2.SIFT_create()
    # find the keypoints and descriptors with SIFT
    kp1, des1 = sift.detectAndCompute(image,None)
    # kp2, des2 = sift.detectAndCompute(img2,None)
    print(des1.shape)
    print(kp1)
    """

    print(points.T.shape)
    points_to_project = np.r_[points.T, np.zeros((1, points.shape[0]))]

    best_RT_matrix = None
    best_rtmse = sys.float_info.max
    best_fitting_points = []

    for i in range(500000):
        points_to_test = points[np.random.choice(points.shape[0], size=(4,), replace=False)]
        
        tennis_court_to_test = tennis_court_model_points_reshaped[np.random.choice(tennis_court_model_points_reshaped.shape[0], size=(4,), replace=False)]
        # tennis_court_to_test = tennis_court_model_points_reshaped[[0,3,4,5]]

        RT_matrix = cv2.getPerspectiveTransform(points_to_test, tennis_court_to_test)

        # img_wrap = cv2.warpPerspective(image, RT_matrix, (78, 36))
        # print(img_wrap.shape)
        # cv2.imshow('img_wrap', img_wrap)
        # cv2.waitKey(0)


        projected_points = (RT_matrix @ points_to_project).T

        rtmse = 0.0
        fitting_points = []
        for point in tennis_court_to_test:
            distances = np.sum(np.square(projected_points[:,0:2] - point), axis=1)
            min_point = np.argmin(distances)
            fitting_points.append(min_point)
            rtmse += distances[min_point]
        
        """
        img = np.copy(image)
        for point in fitting_points:
            img = cv2.circle(img, points[point].astype(np.int32), 5, (255, 0, 0), -1)
        cv2.imshow('window', img)
        cv2.waitKey(0)
        """

        if best_rtmse > rtmse:
            best_rtmse = rtmse
            best_RT_matrix = RT_matrix
            best_fitting_points = fitting_points
    
    best_fitting_points = np.asarray(best_fitting_points)

    img = np.copy(image)
    for point in best_fitting_points:
        img = cv2.circle(img, points[point].astype(np.int32), 5, (255, 0, 0), -1)
    cv2.imshow('window', img)
    cv2.waitKey(0)
    
    print("best_rtmse: ", best_rtmse)
    img_wrap = cv2.warpPerspective(image, RT_matrix, (312, 144))
    cv2.imshow('img_wrap', img_wrap)
    cv2.waitKey(0)

        

def model_loading(cfg):
    logger = logging.getLogger("hawp.testing")
    device = cfg.MODEL.DEVICE
    model = WireframeDetector(cfg)
    model = model.to(device)

    checkpointer = DetectronCheckpointer(cfg,
                                         model,
                                         save_dir=cfg.OUTPUT_DIR,
                                         save_to_disk=True,
                                         logger=logger)
    _ = checkpointer.load()
    model = model.eval()
    return model, device

def test(cfg, args):
    model, device = model_loading(cfg)

    if args.img == "":
        if args.img_directory == "":
            print("Image or image directory must be specify")
            sys.exit(1)
        base_output_path = ""
        if args.output_path != "":
            os.makedirs(args.output_path, exist_ok=True)
            base_output_path = args.output_path
        for impath in os.listdir(args.img_directory):
            print("Predicting image ", os.path.join(args.img_directory,impath))
            if impath.endswith('.jpg') or impath.endswith('.jpeg'):
                output_path = ""
                if base_output_path != "":
                    output_path = os.path.join(base_output_path, impath)
                test_single_image(cfg, os.path.join(args.img_directory, impath), model, device, output_path = output_path, threshold = args.threshold)
    else:
        output_path = ""
        if args.output_path != "":
            output_path = args.output_path
        test_single_image(cfg, os.path.join(args.img_directory, impath), model, device, output_path = output_path, threshold = args.threshold)

if __name__ == "__main__":
    args = argument_parsing()
    cfg.merge_from_file(args.config_file)
    cfg.freeze()
    
    output_dir = cfg.OUTPUT_DIR
    logger = setup_logger('hawp', output_dir)
    logger.info(args)
    logger.info("Loaded configuration file {}".format(args.config_file))

    test(cfg, args)