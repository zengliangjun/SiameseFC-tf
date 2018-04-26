from __future__ import division
import sys
import os
import numpy as np
from PIL import Image
import src.siamese as siam
from src.tracker import tracker_v2
from src.parse_arguments import parse_arguments
from src.region_to_bbox import region_to_bbox
import tensorflow as tf
from PIL import Image
import time
import cv2
"""
	tracking procedure:
	1,input a image sequence of a vedio
	2,z = first frame
	3,x = next img
	4,pad and crop z,x, generate three version of diffenrent scale(crop to different size and rescale to a certain size)
	5,calculate score map * 3
	6,fetch the max score and update size(for step 4)(scale)
	7,cosine window
	8,update pos_x, pos_y
	9,z = x, with new size, pos_x, pos_y
	10,goto step 3

"""



def main():
	# avoid printing TF debugging information
	os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
	# TODO: allow parameters from command line or leave everything in json files?
	hp, evaluation, run, env, design = parse_arguments()
	# Set size for use with tf.image.resize_images with align_corners=True.
	# For example,
	#   [1 4 7] =>   [1 2 3 4 5 6 7]    (length 3*(3-1)+1)
	# instead of
	# [1 4 7] => [1 1 2 3 4 5 6 7 7]  (length 3*3)
	final_score_sz = hp.response_up * (design.score_sz - 1) + 1
	# build TF graph once for all
	gt, frame_name_list, _, _ = _init_video(env, evaluation, evaluation.video)
	
	frame_sz = [i for i in cv2.imread(frame_name_list[0]).shape]
	
	siamNet = siam.Siamese(batch_size = 1);
	image, z_crops, x_crops, templates_z, scores, loss, _, distance_to_gt, summary, templates_x, max_pos_x, max_pos_y = siamNet.build_tracking_graph_train(final_score_sz, design, env, hp, frame_sz)


	
	pos_x, pos_y, target_w, target_h = region_to_bbox(gt[evaluation.start_frame])
	bboxes, speed = tracker_v2(hp, run, design, frame_name_list, pos_x, pos_y, target_w, target_h, final_score_sz,
		                    image, templates_z, scores, evaluation.start_frame,  path_ckpt = os.path.join(design.saver_folder, design.path_ckpt), siamNet = siamNet)
	_, precision, precision_auc, iou = _compile_results(gt, bboxes, evaluation.dist_threshold)
	print(evaluation.video + \
		  ' -- Precision ' + "(%d px)" % evaluation.dist_threshold + ': ' + "%.2f" % precision +\
		  ' -- Precision AUC: ' + "%.2f" % precision_auc + \
		  ' -- IOU: ' + "%.2f" % iou + \
		  ' -- Speed: ' + "%.2f" % speed + ' --')



def _compile_results(gt, bboxes, dist_threshold):
	l = np.size(bboxes, 0)
	gt4 = np.zeros((l, 4))
	new_distances = np.zeros(l)
	new_ious = np.zeros(l)
	n_thresholds = 50
	precisions_ths = np.zeros(n_thresholds)

	for i in range(l):
		gt4[i, :] = region_to_bbox(gt[i, :], center=False)
		new_distances[i] = _compute_distance(bboxes[i, :], gt4[i, :])
		new_ious[i] = _compute_iou(bboxes[i, :], gt4[i, :])

	# what's the percentage of frame in which center displacement is inferior to given threshold? (OTB metric)
	precision = sum(new_distances < dist_threshold)/np.size(new_distances) * 100

	# find above result for many thresholds, then report the AUC
	thresholds = np.linspace(0, 25, n_thresholds+1)
	thresholds = thresholds[-n_thresholds:]
	# reverse it so that higher values of precision goes at the beginning
	thresholds = thresholds[::-1]
	for i in range(n_thresholds):
		precisions_ths[i] = sum(new_distances < thresholds[i])/np.size(new_distances)

	# integrate over the thresholds
	precision_auc = np.trapz(precisions_ths)    

	# per frame averaged intersection over union (OTB metric)
	iou = np.mean(new_ious) * 100

	return l, precision, precision_auc, iou


def _init_video(env, evaluation, video):
	video_folder = os.path.join(env.root_dataset, evaluation.dataset, video)
	frame_name_list = [f for f in os.listdir(video_folder) if f.endswith(".jpg")]
	frame_name_list = [os.path.join(env.root_dataset, evaluation.dataset, video, '') + s for s in frame_name_list]
	frame_name_list.sort()
	with Image.open(frame_name_list[0]) as img:
		frame_sz = np.asarray(img.size) #im.size ⇒ (width, height)
		frame_sz[1], frame_sz[0] = frame_sz[0], frame_sz[1]

	# read the initialization from ground truth
	gt_file = os.path.join(video_folder, 'groundtruth.txt')
	gt = np.genfromtxt(gt_file, delimiter=',')
	n_frames = len(frame_name_list)
	assert n_frames == len(gt), 'Number of frames and number of GT lines should be equal.'

	return gt, frame_name_list, frame_sz, n_frames


def _compute_distance(boxA, boxB):
	a = np.array((boxA[0]+boxA[2]/2, boxA[1]+boxA[3]/2))
	b = np.array((boxB[0]+boxB[2]/2, boxB[1]+boxB[3]/2))
	dist = np.linalg.norm(a - b)

	assert dist >= 0
	assert dist != float('Inf')

	return dist


def _compute_iou(boxA, boxB):
	# determine the (x, y)-coordinates of the intersection rectangle
	xA = max(boxA[0], boxB[0])
	yA = max(boxA[1], boxB[1])
	xB = min(boxA[0] + boxA[2], boxB[0] + boxB[2])
	yB = min(boxA[1] + boxA[3], boxB[1] + boxB[3])

	if xA < xB and yA < yB:
		# compute the area of intersection rectangle
		interArea = (xB - xA) * (yB - yA)
		# compute the area of both the prediction and ground-truth
		# rectangles
		boxAArea = boxA[2] * boxA[3]
		boxBArea = boxB[2] * boxB[3]
		# compute the intersection over union by taking the intersection
		# area and dividing it by the sum of prediction + ground-truth
		# areas - the intersection area
		iou = interArea / float(boxAArea + boxBArea - interArea)
	else:
		iou = 0

	assert iou >= 0
	assert iou <= 1.01

	return iou


if __name__ == '__main__':
	sys.exit(main())

