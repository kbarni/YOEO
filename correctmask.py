import cv2
import sys
import os

path = sys.argv[1]

for file_name in os.listdir(path):
    print(f"Processing {file_name}")
    imgpath = os.path.join(path, file_name)
    msk = cv2.imread(imgpath,cv2.IMREAD_GRAYSCALE)
    ret,thr = cv2.threshold(msk,3,255,cv2.THRESH_TOZERO_INV)
    cv2.imwrite(imgpath,thr)