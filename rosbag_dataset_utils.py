####### ROS UTILITIES #######
# IMPORTS
# system
import os
import pdb
# math
import numpy as np
import numpy.linalg as la
from bisect import bisect_left
# Vision
import cv2
from cv_bridge import CvBridge, CvBridgeError
# ros
import rospy, rosbag
from geometry_msgs.msg import PoseStamped, Twist, Pose
from sensor_msgs.msg import Image, CameraInfo
from std_msgs.msg import Float32MultiArray, MultiArrayDimension
from scipy.spatial.transform import Rotation as R

def pose_to_tf(pose):
    """
    tf_w_q (w:world, q:quad) s.t. if a point is in the quad frame (p_q) then
    the point transformed to be in the world frame is p_w = tf_w_q * p_q.
    """
    tf_w_q = quat_to_tf([pose.orientation.w, pose.orientation.x, pose.orientation.y, pose.orientation.z])
    tf_w_q[0:3, 3] = np.array([pose.position.x, pose.position.y, pose.position.z])
    return tf_w_q


def quat_to_rotm(quat):
    """ 
    calculate the rotation matrix of a given quaternion (frames assumed to be consistant 
    with the UKF state quaternion). First element of quat is the scalar.
    """
    return R.from_quat(np.roll(np.reshape(quat, (-1, 4)),3,axis=1)).as_dcm()


def quat_to_tf(quat):
    """ 
    calculate the rotation matrix of a given quaternion (frames assumed to be consistant 
    with the UKF state quaternion). First element of quat is the scalar.
    """
    tf_out = np.eye(4)
    tf_out[0:3, 0:3] = quat_to_rotm(quat)
    return tf_out


def invert_tf(tf):
    tf[0:3, 0:3] = tf[0:3, 0:3].T
    tf[0:3, 3] = np.matmul(-tf[0:3, 0:3], tf[0:3, 3])
    return tf


def find_closest_by_time(time_to_match, time_list, message_list=None):
    """
    time_to_match : time we want to get the closest match to [float]
    time_list : list of times [list<floats>]
    message_list : list of messages [list<ros msg>]
    Assumes lists are sorted earlier to later. Returns closest item in list by time. If two numbers are equally close, return the smallest number.
    Adapted from https://stackoverflow.com/questions/12141150/from-list-of-integers-get-number-closest-to-a-given-value/12141511#12141511
    """
    if not message_list:
        message_list = time_list
    pos = bisect_left(time_list, time_to_match)
    if pos == 0:
        return message_list[0], 0
    if pos == len(time_list):
        return message_list[-1], len(message_list) - 1
    before = time_list[pos - 1]
    after = time_list[pos]
    if after - time_to_match < time_to_match - before:
       return message_list[pos], pos
    else:
       return message_list[pos - 1], pos - 1


def create_dir_if_missing(my_dir):
    """ if directory does not exist, create it """
    if not os.path.exists(my_dir):
        os.makedirs(my_dir)


def read_rosbag(rosbag_dir, input_rosbag, topics, tf_cam_ego):
    """
    -Rosbag must contain ego drone's poses, ado drone's poses, the images, and the camera info
    -Each of these will be returned as a list of ros messages and a list of corresponding times,
     except for the images which dont need times and the camera info which will be a camera object
    """
    ego_pose_msg_list = []
    ego_pose_msg_time_list = []
    ado_pose_msg_list = []
    ado_pose_msg_time_list = []
    image_msg_list = []
    time_0 = -1
    K = None
    bag_data = rosbag.Bag(rosbag_dir + '/' + input_rosbag)
    for topic, msg, t in bag_data.read_messages():
        if topic == topics['image']:
            if time_0 < 0:
                time_0 = t.to_sec()
            image_msg_list.append(msg)
        elif topic == topics['ego_pose_gt']:
            ego_pose_msg_list.append(msg)
            ego_pose_msg_time_list.append(t.to_sec())
        elif topic == topics['ado_pose_gt']:
            ado_pose_msg_list.append(msg)
            ado_pose_msg_time_list.append(t.to_sec())
        elif K is None and topic == topics['camera_info']:
            im_w = msg.width
            im_h = msg.height
            K = np.reshape(msg.K, (3, 3))
            dist_coefs = np.reshape(msg.D, (5,))
    print("done reading rosbag")
    my_camera = camera(K, dist_coefs, im_w, im_h, tf_cam_ego)
    return ego_pose_msg_list, ego_pose_msg_time_list, ado_pose_msg_list, ado_pose_msg_time_list, image_msg_list, my_camera


class camera:
    def __init__(self, K, dist_coefs, im_w, im_h, tf_cam_ego):
        """
        K: camera intrinsic matrix 
        tf_cam_ego: camera pose relative to the ego_quad (fixed)
        fov_horz/fov_vert: Angular field of view (IN RADIANS) for horizontal and vertical directions
        fov_lim_per_depth: how the boundary of the fov (width, heigh) changes per depth
        """
        self.K = K
        self.dist_coefs = dist_coefs
        self.im_w = im_w
        self.im_h = im_h
        self.new_camera_matrix, _ = cv2.getOptimalNewCameraMatrix(self.K, self.dist_coefs, (im_w, im_h), 0, (im_w, im_h))

        self.K_inv = la.inv(self.K)
        self.new_camera_matrix_inv = la.inv(self.new_camera_matrix)
        self.tf_cam_ego = tf_cam_ego

        self.bridge = CvBridge()

    def pnt3d_to_pix(self, pnt_c):
        """
        input: assumes pnt in camera frame
        output: [row, col] i.e. the projection of xyz onto camera plane
        """
        rc = np.matmul(self.new_camera_matrix, np.reshape(pnt_c[0:3], 3, 1))
        rc = np.array([rc[1], rc[0]]) / rc[2]
        return rc

    def undistort_ros_image(self, img_msg):
        """
        input: image as a ros message
        output: undistorted image as opencv format
        """
        image_cv = self.bridge.imgmsg_to_cv2(img_msg, desired_encoding="passthrough")
        image_cv = cv2.undistort(image_cv, self.K, self.dist_coefs, None, self.new_camera_matrix)
        return image_cv
