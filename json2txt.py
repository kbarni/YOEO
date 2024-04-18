#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Jun  8 13:30:17 2021

@author: starplatinum
"""

import json
import os
import sys
import numpy as np
import glob
from tqdm import tqdm  
#Numéro des classes:
#0: grappe symptomatique 
#1: feuille symptomatique
#2: feuille ESCA    
#3: facteur confondant
#dossier comprenant TOUTES les annotations

################################################################################

# size=[2432, 2112] 
size=[2432, 2128] 
# size=[2432, 1832] 
# size=[2560, 1920] 
if len(sys.argv)<2:
    print("Useage: json2txt folder")
    exit()
filesall = sys.argv[1]

################################################################################




labnames = glob.glob(f'{filesall}/*.json')
filesallname =[]
for i in labnames :  filesallname.append(os.path.basename(i))
# filesallname=os.listdir(filesall) #On récupère les noms
# fichier = open("/home/starplatinum/Desktop/All.txt", "a") #ouverture du fichier txt

if not os.path.exists(filesall+'/txt/'):
    os.mkdir(filesall+'/txt/')


def convert(size, box):
    dw = 1./size[0]
    dh = 1./size[1]
    x = (box[0] + box[1])/2.0
    y = (box[2] + box[3])/2.0
    w = box[1] - box[0]
    h = box[3] - box[2]
    x = x*dw
    w = w*dw
    y = y*dh
    h = h*dh
    return (x,y,w,h)
count =0
lines =[]
for file in tqdm(filesallname): #on parcourt tous les fichiers du dossier 
    with open(filesall+file,'r',encoding='cp437') as json_data:
        if(file != 'Classes.json'):
            if(file[-1]=='n'): #Pour ne pas lire les images, juste les fichiers json
                # fichier.write(file[:-4]+'txt') #Ecriture du nom de l'image
                data_dict=json.load(json_data) #Lecture du json
                fichier = open(filesall+"/txt//%stxt" % file[:-4], "a") #ouverture du fichier txt

                for rectangle in data_dict['shapes']: #On parcourt toutes les annotations
                    if(rectangle["shape_type"]=='rectangle') :  #pour enlever les lignes des rameaux 
                    
                        # if(rectangle["label"]!="grappe symptomatique") & (rectangle["label"]!="ESCA tres precoce")& (rectangle["label"]!="ESCA legerement marquee") & (rectangle["label"]!="feuille fd ratee")  :
                        if(rectangle["label"]!="grappe symptomatique") & (rectangle["label"]!="ESCA tres precoce") :
                        # if(rectangle["label"]!="grappe symptomatique") & (rectangle["label"]!="feuille fd ratee") :
                        # if(rectangle["label"]!="grappe symptomatique") :
                            x1=int(np.round(rectangle["points"][0][0]))
                            y1=int(np.round(rectangle["points"][0][1]))
                            x2=int(np.round(rectangle["points"][1][0]))
                            y2=int(np.round(rectangle["points"][1][1]))
                            
                            xmin = min(x1,x2)
                            xmax = max(x1,x2)
                            ymin = min(y1,y2)
                            ymax = max(y1,y2)
    
                            w= int(size[0])
                            h= int(size[1])  
                            
                            b = (xmin, xmax, ymin, ymax)
                            bb = convert((w,h), b)
                            lines.append(bb)
                            
                            
                            #Attribution d'un numéro pour chaque classe
                            classe=rectangle["label"]
                            num_classe=3
                            if(classe == "feuille fd ratee"):
                                num_classe=0
                            if(classe == "feuille symptomatique") :
                                num_classe=0

                            # if(classe == "ESCA tres precoce"):
                            #     num_classe=1
                            if(classe == "ESCA legerement marquee"):
                                num_classe=1
                            if(classe == "feuille ESCA"):
                                num_classe=1

                            if(classe == "facteur confondant"):
                                num_classe=2
                            if(classe == "facteur confondant +"):
                                 num_classe=2
                                                     
                            # if(classe == "grappe symptomatique"):
                            #     num_classe=3
                            # fichier = open("/home/starplatinum/Desktop/All.txt", "a") #ouverture du fichier txt
                            # fichier = open("/home/prospectfd/Bureau/out_2//%stxt" % file[:-4], "a") #ouverture du fichier txt
    
                            fichier.write(" {} {:.6f} {:.6f} {:.6f} {:.6f}\n".format(num_classe,bb[0],bb[1],bb[2],bb[3])) #Ecriture dans le fichier txt
                            # fichier.write(" {},{},{},{},{}".format(x1,y1,x2,y2,num_classe)) #Ecriture dans le fichier txt
                fichier.close()
                        # del fichier
# fichier.close() #Fermeture du fichier