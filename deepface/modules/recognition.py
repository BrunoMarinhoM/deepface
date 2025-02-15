# built-in dependencies
import os
import pickle
from typing import List, Union, Optional
import time

# 3rd party dependencies
import numpy as np
import pandas as pd
from tqdm import tqdm

# project dependencies
from deepface.commons.logger import Logger
from deepface.commons.sets_utils import is_subset, intersection
from deepface.modules import representation, detection, modeling, verification
from deepface.models.FacialRecognition import FacialRecognition

logger = Logger(module="deepface/modules/recognition.py")


def find(
    img_path: Union[str, np.ndarray],
    db_path: str,
    model_name: str = "VGG-Face",
    distance_metric: str = "cosine",
    enforce_detection: bool = True,
    detector_backend: str = "opencv",
    align: bool = True,
    expand_percentage: int = 0,
    threshold: Optional[float] = None,
    normalization: str = "base",
    silent: bool = True,
    refresh_database: bool = True,
    updated_images: list[str] = []
) -> List[pd.DataFrame]:
    """
    Identify individuals in a database

    Args:
        img_path (str or np.ndarray): The exact path to the image, a numpy array in BGR format,
            or a base64 encoded image. If the source image contains multiple faces, the result will
            include information for each detected face.

        db_path (string): Path to the folder containing image files. All detected faces
            in the database will be considered in the decision-making process.

        model_name (str): Model for face recognition. Options: VGG-Face, Facenet, Facenet512,
            OpenFace, DeepFace, DeepID, Dlib, ArcFace and SFace

        distance_metric (string): Metric for measuring similarity. Options: 'cosine',
            'euclidean', 'euclidean_l2'.

        enforce_detection (boolean): If no face is detected in an image, raise an exception.
            Default is True. Set to False to avoid the exception for low-resolution images.

        detector_backend (string): face detector backend. Options: 'opencv', 'retinaface',
            'mtcnn', 'ssd', 'dlib', 'mediapipe', 'yolov8'.

        align (boolean): Perform alignment based on the eye positions.

        expand_percentage (int): expand detected facial area with a percentage (default is 0).

        threshold (float): Specify a threshold to determine whether a pair represents the same
            person or different individuals. This threshold is used for comparing distances.
            If left unset, default pre-tuned threshold values will be applied based on the specified
            model name and distance metric (default is None).

        normalization (string): Normalize the input image before feeding it to the model.
            Default is base. Options: base, raw, Facenet, Facenet2018, VGGFace, VGGFace2, ArcFace

        silent (boolean): Suppress or allow some log messages for a quieter analysis process.

        refresh_database (boolean): Checks if the files contained in the database directory/folder
        were altered and updates the pkl file if the updated_images is empty, else it will check 
        specifically for the images in the path (should be the same as the database) and will try 
        to upload/reload them into the picke file

        updated_images (list[str]): list of the images in the same path as the db (example: 
        ['home/user/database/img_92.jpg'] would be a valid list if and only if the database also 
        was given by 'home/user/database/') that sould be updated

        

    Returns:
        results (List[pd.DataFrame]): A list of pandas dataframes. Each dataframe corresponds
            to the identity information for an individual detected in the source image.
            The DataFrame columns include:

            - 'identity': Identity label of the detected individual.

            - 'target_x', 'target_y', 'target_w', 'target_h': Bounding box coordinates of the
                    target face in the database.

            - 'source_x', 'source_y', 'source_w', 'source_h': Bounding box coordinates of the
                    detected face in the source image.

            - 'threshold': threshold to determine a pair whether same person or different persons

            - 'distance': Similarity score between the faces based on the
                    specified model and distance metric
    """

    tic = time.time()

    # -------------------------------
    if os.path.isdir(db_path) is not True:
        raise ValueError("Passed db_path does not exist!")

    model: FacialRecognition = modeling.build_model(model_name)
    target_size = model.input_shape

    # ---------------------------------------

    file_name = f"representations_{model_name}.pkl"
    file_name = file_name.replace("-", "_").lower()
    datastore_path = os.path.join(db_path, file_name)
    representations = []

    df_cols = [
        "identity",
        f"{model_name}_representation",
        "target_x",
        "target_y",
        "target_w",
        "target_h",
    ]

    # Ensure the proper pickle file exists
    if not os.path.exists(datastore_path):
        with open(datastore_path, "wb") as f:
            pickle.dump([], f)

    # Load the representations from the pickle file
    with open(datastore_path, "rb") as f:
        representations = pickle.load(f)

    # Check if the representations are out-of-date
    if len(representations) > 0:
        if len(representations[0]) != len(df_cols):
            raise ValueError(
                f"Seems existing {datastore_path} is out-of-the-date."
                "Please delete it and re-run."
            )
        pickled_images = [representation[0] for representation in representations]
    else:
        pickled_images = []

    # Get the list of images on storage
    storage_images = __list_images(path=db_path)

    # Enforce data consistency amongst on disk images and pickle file
    must_save_pickle = False
    if refresh_database:
        if len(updated_images) == 0:
            #update in the regular way (checks the whole database)
            new_images = list(set(storage_images) - set(pickled_images)) # images added to storage
            old_images = list(set(pickled_images) - set(storage_images)) # images removed from storage

        if (not is_subset(set(updated_images), set(pickled_images)) and 
                not is_subset(set(updated_images), set(storage_images))): 

            raise ValueError(f"""
            The following files to be updated_images are neither in the pickled_images
            not in the database directory: {updated_images}
            """)

        else:
            old_images = list(intersection(set(pickled_images), set(updated_images)))
            new_images = list(intersection(set(storage_images), set(updated_images)))

    else:
        new_images = []
        old_images = []

    if not silent and (len(new_images) > 0 or len(old_images) > 0):
        logger.info(f"Found {len(new_images)} new images and {len(old_images)} removed images")

    # remove old images first
    if len(old_images)>0:
        representations = [rep for rep in representations if rep[0] not in old_images]
        must_save_pickle = True

    # find representations for new images
    if len(new_images)>0:
        representations += __find_bulk_embeddings(
            employees=new_images,
            model_name=model_name,
            target_size=target_size,
            detector_backend=detector_backend,
            enforce_detection=enforce_detection,
            align=align,
            normalization=normalization,
            silent=silent,
        ) # add new images
        must_save_pickle = True

    if must_save_pickle:
        with open(datastore_path, "wb") as f:
            pickle.dump(representations, f)
        if not silent:
            logger.info(f"There are now {len(representations)} representations in {file_name}")

    # Should we have no representations bailout
    if len(representations) == 0:
        if not silent:
            toc = time.time()
            logger.info(f"find function duration {toc - tic} seconds")
        return []

    # ----------------------------
    # now, we got representations for facial database
    df = pd.DataFrame(
        representations,
        columns=df_cols,
    )

    # img path might have more than once face
    source_objs = detection.extract_faces(
        img_path=img_path,
        target_size=target_size,
        detector_backend=detector_backend,
        grayscale=False,
        enforce_detection=enforce_detection,
        align=align,
        expand_percentage=expand_percentage,
    )

    resp_obj = []

    for source_obj in source_objs:
        source_img = source_obj["face"]
        source_region = source_obj["facial_area"]
        target_embedding_obj = representation.represent(
            img_path=source_img,
            model_name=model_name,
            enforce_detection=enforce_detection,
            detector_backend="skip",
            align=align,
            normalization=normalization,
        )

        target_representation = target_embedding_obj[0]["embedding"]

        result_df = df.copy()  # df will be filtered in each img
        result_df["source_x"] = source_region["x"]
        result_df["source_y"] = source_region["y"]
        result_df["source_w"] = source_region["w"]
        result_df["source_h"] = source_region["h"]

        distances = []
        for _, instance in df.iterrows():
            source_representation = instance[f"{model_name}_representation"]
            if source_representation is None:
                distances.append(float("inf")) # no representation for this image
                continue

            target_dims = len(list(target_representation))
            source_dims = len(list(source_representation))
            if target_dims != source_dims:
                raise ValueError(
                    "Source and target embeddings must have same dimensions but "
                    + f"{target_dims}:{source_dims}. Model structure may change"
                    + " after pickle created. Delete the {file_name} and re-run."
                )

            if distance_metric == "cosine":
                distance = verification.find_cosine_distance(
                    source_representation, target_representation
                )
            elif distance_metric == "euclidean":
                distance = verification.find_euclidean_distance(
                    source_representation, target_representation
                )
            elif distance_metric == "euclidean_l2":
                distance = verification.find_euclidean_distance(
                    verification.l2_normalize(source_representation),
                    verification.l2_normalize(target_representation),
                )
            else:
                raise ValueError(f"invalid distance metric passes - {distance_metric}")

            distances.append(distance)

            # ---------------------------
        target_threshold = threshold or verification.find_threshold(model_name, distance_metric)

        result_df["threshold"] = target_threshold
        result_df["distance"] = distances

        result_df = result_df.drop(columns=[f"{model_name}_representation"])
        # pylint: disable=unsubscriptable-object
        result_df = result_df[result_df["distance"] <= target_threshold]
        result_df = result_df.sort_values(by=["distance"], ascending=True).reset_index(drop=True)

        resp_obj.append(result_df)

    # -----------------------------------

    if not silent:
        toc = time.time()
        logger.info(f"find function duration {toc - tic} seconds")

    return resp_obj


def __list_images(path: str) -> List[str]:
    """
    List images in a given path
    Args:
        path (str): path's location
    Returns:
        images (list): list of exact image paths
    """
    images = []
    for r, _, f in os.walk(path):
        for file in f:
            if file.lower().endswith((".jpg", ".jpeg", ".png")):
                exact_path = os.path.join(r, file)
                images.append(exact_path)
    return images


def __find_bulk_embeddings(
    employees: List[str],
    model_name: str = "VGG-Face",
    target_size: tuple = (224, 224),
    detector_backend: str = "opencv",
    enforce_detection: bool = True,
    align: bool = True,
    expand_percentage: int = 0,
    normalization: str = "base",
    silent: bool = False,
):
    """
    Find embeddings of a list of images

    Args:
        employees (list): list of exact image paths

        model_name (str): facial recognition model name

        target_size (tuple): expected input shape of facial recognition model

        detector_backend (str): face detector model name

        enforce_detection (bool): set this to False if you
            want to proceed when you cannot detect any face

        align (bool): enable or disable alignment of image
            before feeding to facial recognition model

        expand_percentage (int): expand detected facial area with a
            percentage (default is 0).

        normalization (bool): normalization technique

        silent (bool): enable or disable informative logging
    Returns:
        representations (list): pivot list of embeddings with
            image name and detected face area's coordinates
    """
    representations = []
    for employee in tqdm(
        employees,
        desc="Finding representations",
        disable=silent,
    ):
        try:
            img_objs = detection.extract_faces(
                img_path=employee,
                target_size=target_size,
                detector_backend=detector_backend,
                grayscale=False,
                enforce_detection=enforce_detection,
                align=align,
                expand_percentage=expand_percentage,
            )
        except ValueError as err:
            logger.error(
                f"Exception while extracting faces from {employee}: {str(err)}"
            )
            img_objs = []

        if len(img_objs) == 0:
            logger.warn(f"No face detected in {employee}. It will be skipped in detection.")
            representations.append((employee, None, 0, 0, 0, 0))
        else:
            for img_obj in img_objs:
                img_content = img_obj["face"]
                img_region = img_obj["facial_area"]
                embedding_obj = representation.represent(
                    img_path=img_content,
                    model_name=model_name,
                    enforce_detection=enforce_detection,
                    detector_backend="skip",
                    align=align,
                    normalization=normalization,
                )

                img_representation = embedding_obj[0]["embedding"]
                representations.append((
                    employee,
                    img_representation,
                    img_region["x"],
                    img_region["y"],
                    img_region["w"],
                    img_region["h"]
                    ))

    return representations
