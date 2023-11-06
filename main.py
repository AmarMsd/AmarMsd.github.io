import os
import cv2
import json
import subprocess
import numpy as np
from ultralytics import YOLO
from ultralytics.engine.results import Results
from _collections import deque
from deep_sort_realtime.deepsort_tracker import DeepSort
from stqdm import stqdm
import streamlit as st
import datetime
import time
import pandas as pd
import plotly.express as px

# colors for visualization for image visualization
COLORS = [(56, 56, 255), (151, 157, 255), (31, 112, 255), (29, 178, 255), (49, 210, 207), (10, 249, 72), (23, 204, 146),
          (134, 219, 61), (52, 147, 26), (187, 212, 0), (168, 153,
                                                         44), (255, 194, 0), (147, 69, 52), (255, 115, 100),
          (236, 24, 0), (255, 56, 132), (133, 0, 82), (255, 56, 203), (200, 149, 255), (199, 55, 255)]


def result_to_json(result: Results, tracker=None):
    """
    Convert result from ultralytics YOLOv8 prediction to json format
    Parameters:
        result: Results from ultralytics YOLOv8 prediction
        tracker: DeepSort tracker
    Returns:
        result_list_json: detection result in json format
    """
    len_results = len(result.boxes)
    result_list_json = [
        {
            'class_id': int(result.boxes.cls[idx]),
            'class': result.names[int(result.boxes.cls[idx])],
            'confidence': float(result.boxes.conf[idx]),
            'bbox': {
                'x_min': int(result.boxes.data[idx][0]),
                'y_min': int(result.boxes.data[idx][1]),
                'x_max': int(result.boxes.data[idx][2]),
                'y_max': int(result.boxes.data[idx][3]),
            },
        } for idx in range(len_results)
    ]

    if result.masks is not None:
        for idx in range(len_results):
            result_list_json[idx]['mask'] = cv2.resize(result.masks.data[idx].cpu(
            ).numpy(), (result.orig_shape[1], result.orig_shape[0])).tolist()
            result_list_json[idx]['segments'] = result.masks.xyn[idx].tolist()
    if result.keypoints is not None:
        for idx in range(len_results):
            result_list_json[idx]['keypoints'] = result.keypoints.xyn[idx].tolist()
    if tracker is not None:
        bbs = [
            (
                [
                    result_list_json[idx]['bbox']['x_min'],
                    result_list_json[idx]['bbox']['y_min'],
                    result_list_json[idx]['bbox']['x_max'] -
                    result_list_json[idx]['bbox']['x_min'],
                    result_list_json[idx]['bbox']['y_max'] -
                    result_list_json[idx]['bbox']['y_min']
                ],
                result_list_json[idx]['confidence'],
                result_list_json[idx]['class'],
            ) for idx in range(len_results)
        ]
        tracks = tracker.update_tracks(bbs, frame=result.orig_img)
        for idx in range(len(result_list_json)):
            track_idx = next((i for i, track in enumerate(tracks) if track.det_conf is not None and np.isclose(
                track.det_conf, result_list_json[idx]['confidence'])), -1)
            if track_idx != -1:
                result_list_json[idx]['object_id'] = int(
                    tracks[track_idx].track_id)
    return result_list_json


def view_result(result: Results, result_list_json, centers=None):
    """
    Visualize result from ultralytics YOLOv8 prediction using default visualization function
    Parameters:
        result: Results from ultralytics YOLOv8 prediction
        result_list_json: detection result in json format
        centers: list of deque of center points of bounding boxes
    Returns:
        result_image_default: result image from default visualization function
    """
    image = result.plot(labels=False, line_width=2)
    for result in result_list_json:
        class_color = COLORS[result['class_id'] % len(COLORS)]
        text = f"{result['class']} {result['object_id']}: {result['confidence']:.2f}" if 'object_id' in result else f"{result['class']}: {result['confidence']:.2f}"
        (text_width, text_height), baseline = cv2.getTextSize(
            text, cv2.FONT_HERSHEY_SIMPLEX, 0.75, 2)
        cv2.rectangle(image, (result['bbox']['x_min'], result['bbox']['y_min'] - text_height - baseline),
                      (result['bbox']['x_min'] + text_width, result['bbox']['y_min']), class_color, -1)
        cv2.putText(image, text, (result['bbox']['x_min'], result['bbox']
                    ['y_min'] - baseline), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
        if 'object_id' in result and centers is not None:
            centers[result['object_id']].append(
                (int((result['bbox']['x_min'] + result['bbox']['x_max']) / 2), int((result['bbox']['y_min'] + result['bbox']['y_max']) / 2)))
            for j in range(1, len(centers[result['object_id']])):
                if centers[result['object_id']][j - 1] is None or centers[result['object_id']][j] is None:
                    continue
                thickness = int(np.sqrt(64 / float(j + 1)) * 2)
                cv2.line(image, centers[result['object_id']][j - 1],
                         centers[result['object_id']][j], class_color, thickness)
    return image


def image_processing(frame, model, tracker=None, centers=None):
    """
    Process image frame using ultralytics YOLOv8 model and possibly DeepSort tracker if it is provided
    Parameters:
        frame: image frame
        model: ultralytics YOLOv8 model
        tracker: DeepSort tracker
        centers: list of deque of center points of bounding boxes
    Returns:
        result_image: result image with bounding boxes, class names, confidence scores, object masks, and possibly object IDs
        result_list_json: detection result in json format
    """
    results = model.predict(frame)
    result_list_json = result_to_json(results[0], tracker=tracker)
    result_image = view_result(results[0], result_list_json, centers=centers)
    return result_image, result_list_json


def video_processing(video_file, model, tracker=None, centers=None):
    """
    Process video file using ultralytics YOLOv8 model and possibly DeepSort tracker if it is provided
    Parameters:
        video_file: video file
        model: ultralytics YOLOv8 model
        tracker: DeepSort tracker
        centers: list of deque of center points of bounding boxes
    Returns:
        video_file_name_out: name of output video file
        result_video_json_file: file containing detection result in json format
    """
    results = model.predict(video_file)
    model_name = model.ckpt_path.split('/')[-1].split('.')[0]
    output_folder = os.path.join('output_videos', video_file.split('.')[0])
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
    video_file_name_out = os.path.join(
        output_folder, f"{video_file.split('.')[0]}_{model_name}_output.mp4")
    if os.path.exists(video_file_name_out):
        os.remove(video_file_name_out)
    result_video_json_file = os.path.join(
        output_folder, f"{video_file.split('.')[0]}_{model_name}_output.json")
    if os.path.exists(result_video_json_file):
        os.remove(result_video_json_file)
    json_file = open(result_video_json_file, 'a')
    temp_file = 'temp.mp4'
    video_writer = cv2.VideoWriter(temp_file, cv2.VideoWriter_fourcc(
        *'mp4v'), 30, (results[0].orig_img.shape[1], results[0].orig_img.shape[0]))
    json_file.write('[\n')
    for result in stqdm(results, desc=f"Processing video"):
        result_list_json = result_to_json(result, tracker=tracker)
        result_image = view_result(result, result_list_json, centers=centers)
        video_writer.write(result_image)
        json.dump(result_list_json, json_file, indent=2)
        json_file.write(',\n')
    json_file.write(']')
    video_writer.release()
    subprocess.call(
        args=f"ffmpeg -i {os.path.join('.', temp_file)} -c:v libx264 {os.path.join('.', video_file_name_out)}".split(" "))
    os.remove(temp_file)
    return video_file_name_out, result_video_json_file


if not os.path.exists("models/"):
    os.makedirs("models/")
model_list = [model_name.strip()
              for model_name in open("model_list.txt").readlines()]
st.set_page_config(page_title="YOLOv8 Processing App",
                   layout="wide", page_icon="./favicon-yolo.ico")
st.title("YOLOv8 Processing App")
# create select box for selecting ultralytics YOLOv8 model
model_selectbox = st.empty()
model_select = model_selectbox.selectbox(
    "Select Ultralytics YOLOv8 model", model_list)
print(f"Selected ultralytics YOLOv8 model: {model_select}")
model = YOLO(f'models/{model_select}.pt')  # Model initialization

# tab_image, tab_video,

st.header("Live Stream Processing using YOLOv8")
# Function to read JSON data (simulated)


CAM_ID = st.text_input(
    "Enter a live stream source (number for webcam, RTSP or HTTP(S) URL):", "rtsp://admin:Admin123@110.50.87.169:554/streaming/channels/301")
if CAM_ID.isnumeric():
    CAM_ID = int(CAM_ID)
col_run, col_stop = st.columns(2)
run = col_run.button("Start Live Stream Processing" )
stop = col_stop.button("Stop Live Stream Processing")
if stop:
    run = False
FRAME_WINDOW = st.image([], width=1280)
if run:
    cam = cv2.VideoCapture(CAM_ID)
    cam.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    tracker = DeepSort(max_age=5)
    # centers = [deque(maxlen=30) for _ in range(10000)]
    centers = [deque(maxlen=5) for _ in range(1000)]

    # Define totals for each class
    class_totals = {}
    # Define the date
    current_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    while True:
        ret, image = cam.read()
        if not ret:
            st.error("Failed to capture stream from this camera. Please try again.")
            # continue
            break
        # if run:
        result_image, result_list_json = image_processing(
            image, model, tracker=tracker, centers=centers)

        # Calculate and accumulate totals for each class
        for result in result_list_json:
            class_id = result['class']
            if class_id not in class_totals:
                class_totals[class_id] = 0
            class_totals[class_id] += 1

        # Add the current date to the result_list_json
        for result in result_list_json:
            result['date'] = current_date

    # Combine class totals and result_list_json
        data_with_date = {"totals": class_totals, "data": result_list_json}

        FRAME_WINDOW.image(result_image, channels="BGR", width=1280)

    # Write class_totals and result_list_json to a JSON file
        with open("totals.json", "w") as totals_file:
            json.dump(data_with_date, totals_file, indent=2)

    cam.release()
    tracker.delete_all_tracks()
    centers.clear()
# Function to read JSON data (simulated)


def read_json_data():
    try:
        with open("totals.json", "r") as json_file:
            data = json.load(json_file)
            return data
    except FileNotFoundError:
        return None


st.title("Data Analytic")

# Initialize data history for class totals
class_totals_history = []

# Real-time update interval (in seconds)
update_interval = 5

# Initialize class_totals dan current_date di luar loop

class_totals = {}
current_date = None

# while True:
class_totals_data = read_json_data()
if class_totals_data is not None:
    class_totals = class_totals_data.get("totals")
    # st.text(class_totals)
    date_from_data = class_totals_data.get("date")

    if not date_from_data:
        current_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    else:
        current_date = date_from_data

    class_totals_history.append(
        {"Date": current_date, **class_totals})

# st.text("Data Updated")
# st.bar_chart(data=class_totals_data, x=date_from_data, y=class_totals, color=None,
#              width=0, height=0, use_container_width=True)

st.text(current_date)
st.json(class_totals_history)

if class_totals_history:
    df = pd.DataFrame(class_totals_history)
    st.dataframe(df)

    # chart_data = px.bar(
    #     class_totals_history,
    #     x=class_totals,
    #     y=current_date,
    #     orientation="h",
    #     title="<b> Data Chart </b>",
    #     color_discrete_sequence=["#0083B8"] * len("date"),
    #     template="plotly_white",
    # )
    # st.plotly_chart(chart_data)

else:
    st.text("No data available")

time.sleep(update_interval)
