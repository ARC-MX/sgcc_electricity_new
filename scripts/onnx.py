# import cv2
from PIL import ImageDraw,Image,ImageOps
import numpy as np
import onnxruntime

anchors = [[(116,90),(156,198),(373,326)],[(30,61),(62,45),(59,119)],[(10,13),(16,30),(33,23)]]
anchors_yolo_tiny = [[(81, 82), (135, 169), (344, 319)], [(10, 14), (23, 27), (37, 58)]]
CLASSES=["target"]



class ONNX:
    def __init__(self,onnx_file_name="captcha.onnx"):
        self.onnx_session = onnxruntime.InferenceSession(onnx_file_name) 

    # sigmoid函数
    def sigmoid(self,x):
        s = 1 / (1 + np.exp(-1 * x))
        return s


    # 获取预测正确的类别，以及概率和索引;
    def get_result(self,class_scores):
        class_score = 0
        class_index = 0
        for i in range(len(class_scores)):
            if class_scores[i] > class_score:
                class_index += 1
                class_score = class_scores[i]
        return class_score, class_index


    def xywh2xyxy(self,x):
        # [x, y, w, h] to [x1, y1, x2, y2]
        y = np.copy(x)
        y[:, 0] = x[:, 0] - x[:, 2] / 2
        y[:, 1] = x[:, 1] - x[:, 3] / 2
        y[:, 2] = x[:, 0] + x[:, 2] / 2
        y[:, 3] = x[:, 1] + x[:, 3] / 2
        return y

    # dets:  array [x,6] 6个值分别为x1,y1,x2,y2,score,class
    # thresh: 阈值
    def nms(self,dets, thresh):
        # dets:x1 y1 x2 y2 score class
        # x[:,n]就是取所有集合的第n个数据
        x1 = dets[:, 0]
        y1 = dets[:, 1]
        x2 = dets[:, 2]
        y2 = dets[:, 3]
        # -------------------------------------------------------
        #   计算框的面积
        #	置信度从大到小排序
        # -------------------------------------------------------
        areas = (y2 - y1 + 1) * (x2 - x1 + 1)
        scores = dets[:, 4]
        # print(scores)
        keep = []
        index = scores.argsort()[::-1]  # np.argsort()对某维度从小到大排序
        # [::-1] 从最后一个元素到第一个元素复制一遍。倒序从而从大到小排序

        while index.size > 0:
            i = index[0]
            keep.append(i)
            # -------------------------------------------------------
            #   计算相交面积
            #	1.相交
            #	2.不相交
            # -------------------------------------------------------
            x11 = np.maximum(x1[i], x1[index[1:]])
            y11 = np.maximum(y1[i], y1[index[1:]])
            x22 = np.minimum(x2[i], x2[index[1:]])
            y22 = np.minimum(y2[i], y2[index[1:]])

            w = np.maximum(0, x22 - x11 + 1)
            h = np.maximum(0, y22 - y11 + 1)

            overlaps = w * h
            # -------------------------------------------------------
            #   计算该框与其它框的IOU，去除掉重复的框，即IOU值大的框
            #	IOU小于thresh的框保留下来
            # -------------------------------------------------------
            ious = overlaps / (areas[i] + areas[index[1:]] - overlaps)
            idx = np.where(ious <= thresh)[0]
            index = index[idx + 1]
        return keep


    def draw(self,image, box_data):
        # -------------------------------------------------------
        #	取整，方便画框
        # -------------------------------------------------------

        boxes = box_data[..., :4].astype(np.int32)  # x1 x2 y1 y2
        scores = box_data[..., 4]
        classes = box_data[..., 5].astype(np.int32)
        for box, score, cl in zip(boxes, scores, classes):
            top, left, right, bottom = box
            # print('class: {}, score: {}'.format(CLASSES[cl], score))
            # print('box coordinate left,top,right,down: [{}, {}, {}, {}]'.format(top, left, right, bottom))
            # image = cv2.rectangle(image, (top, left), (right, bottom), (0, 0, 255), 1)
            draw = ImageDraw.Draw(image)
            draw.rectangle([(top, left), (right, bottom)],  outline ="red")
            # cv2.imwrite("result"+str(left)+".jpg",image)
            # font = ImageFont.truetype(font='PingFang.ttc', size=40)
            draw.text(xy=(top, left),text='{0} {1:.2f}'.format(CLASSES[cl], score), fill=(255, 0, 0))

            # image = cv2.putText(image, '{0} {1:.2f}'.format(CLASSES[cl], score), 
            #             (top, left),
            #             cv2.FONT_HERSHEY_SIMPLEX,
            #             0.6, (0, 0, 255), 2)
        return image

    # 获取预测框
    def get_boxes(self, prediction, confidence_threshold=0.7, nms_threshold=0.6):
        # 过滤掉无用的框
        # -------------------------------------------------------
        #   删除为1的维度
        #	删除置信度小于conf_thres的BOX
        # -------------------------------------------------------
        # for i in range(len(prediction)):
        feature_map = np.squeeze(prediction)# 删除数组形状中单维度条目(shape中为1的维度)
        # […,4]：代表了取最里边一层的所有第4号元素，…代表了对:,:,:,等所有的的省略。此处生成：25200个第四号元素组成的数组
        conf = feature_map[..., 4] > confidence_threshold  # 0 1 2 3 4 4是置信度，只要置信度 > conf_thres 的
        box = feature_map[conf == True]  # 根据objectness score生成(n, 5+class_nm)，只留下符合要求的框

        # -------------------------------------------------------
        #   通过argmax获取置信度最大的类别
        # -------------------------------------------------------
        cls_cinf = box[..., 5:]  # 左闭右开（5 6 7 8），就只剩下了每个grid cell中各类别的概率
        cls = []
        for i in range(len(cls_cinf)):
            cls.append(int(np.argmax(cls_cinf[i])))  # 剩下的objecctness score比较大的grid cell，分别对应的预测类别列表
        all_cls = list(set(cls))  # 去重，找出图中都有哪些类别
        # set() 函数创建一个无序不重复元素集，可进行关系测试，删除重复数据，还可以计算交集、差集、并集等。
        # -------------------------------------------------------
        #   分别对每个类别进行过滤
        #   1.将第6列元素替换为类别下标
        #	2.xywh2xyxy 坐标转换
        #	3.经过非极大抑制后输出的BOX下标
        #	4.利用下标取出非极大抑制后的BOX
        # -------------------------------------------------------
        output = []
        for i in range(len(all_cls)):
            curr_cls = all_cls[i]
            curr_cls_box = []
            curr_out_box = []

            for j in range(len(cls)):
                if cls[j] == curr_cls:
                    box[j][5] = curr_cls
                    curr_cls_box.append(box[j][:6])  # 左闭右开，0 1 2 3 4 5

            curr_cls_box = np.array(curr_cls_box)  # 0 1 2 3 4 5 分别是 x y w h score class
            curr_cls_box = self.xywh2xyxy(curr_cls_box)  # 0 1 2 3 4 5 分别是 x1 y1 x2 y2 score class
            curr_out_box = self.nms(curr_cls_box, nms_threshold)  # 获得nms后，剩下的类别在curr_cls_box中的下标

            for k in curr_out_box:
                output.append(curr_cls_box[k])
        output = np.array(output)
        return output

    def letterbox(self, img, new_shape=(640, 640), color=(114, 114, 114), auto=False, scaleFill=False, scaleup=True,
                    stride=32):
        '''图片归一化'''
        # Resize and pad image while meeting stride-multiple constraints
        shape = img.shape[:2]  # current shape [height, width]
        if isinstance(new_shape, int):
            new_shape = (new_shape, new_shape)

        # Scale ratio (new / old)
        r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
        if not scaleup:  # only scale down, do not scale up (for better test mAP)
            r = min(r, 1.0)

        # Compute padding
        ratio = r, r  # width, height ratios

        new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))
        dw, dh = new_shape[1] - new_unpad[0], new_shape[0] - new_unpad[1]  # wh padding

        if auto:  # minimum rectangle
            dw, dh = np.mod(dw, stride), np.mod(dh, stride)  # wh padding
        elif scaleFill:  # stretch
            dw, dh = 0.0, 0.0
            new_unpad = (new_shape[1], new_shape[0])
            ratio = new_shape[1] / shape[1], new_shape[0] / shape[0]  # width, height ratios

        dw /= 2  # divide padding into 2 sides
        dh /= 2

        if shape[::-1] != new_unpad:  # resize
            # img = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)
            img = img.resize(new_unpad)
        top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
        left, right = int(round(dw - 0.1)), int(round(dw + 0.1))

        # img = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)  # add border
        img = ImageOps.expand(img, border=(left, top, right, bottom), fill=0)##left,top,right,bottom
        return img, ratio, (dw, dh)

    def _inference(self,image):
        # org_img = cv2.resize(image, [416, 416]) # resize后的原图 (640, 640, 3)
        org_img = image.resize((416,416))
        # img = cv2.cvtColor(org_img, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
        img = org_img.convert("RGB")
        img = np.array(img).transpose(2, 0, 1)
        img = img.astype(dtype=np.float32)  # onnx模型的类型是type: float32[ , , , ]
        img /= 255.0
        img = np.expand_dims(img, axis=0) # [3, 640, 640]扩展为[1, 3, 640, 640]

        inputs = {self.onnx_session.get_inputs()[0].name: img} 
        prediction = self.onnx_session.run(None, inputs)[0] 
        return prediction, org_img

    def get_distance(self,image,draw=False):
        prediction, org_img = self._inference(image)
        boxes = self.get_boxes(prediction=prediction)
        if len(boxes) == 0:
            print('No gaps were detected.')
            return 0
        else:
            if draw:
                org_img = self.draw(org_img, boxes)
                # cv2.imshow('result', org_img)
                # cv2.imwrite('result.png', org_img)
                org_img.save('result.png')
                # cv2.waitKey(0)
            return int(boxes[..., :4].astype(np.int32)[0][0])

if __name__ == "__main__":
    onnx = ONNX()
    img_path="../assets/background.png"
    # img = cv2.imread(img_path)
    img = Image.open(img_path)
    print(onnx.get_distance(img,True))