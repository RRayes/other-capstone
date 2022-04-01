from roboclaw.roboclaw_3 import Roboclaw
import time
import os
import sys
import cv2
from pupil_apriltags import Detector
import math
import time
import traceback

STATE_GO_FARTHEST = 'state_go_farthest'
STATE_GO_TURN = 'state_go_turn'
STATE_TURN_LEFT = 'state_turn_left'

TAG_FORWARD = 1
TAG_TURN_LEFT = 2


def millis():
    return round(time.time() * 1000)


def rescale(val, in_min, in_max, out_min, out_max):
    return out_min + (val - in_min) * ((out_max - out_min) / (in_max - in_min))


def get_left_right_power_for_tag(tag, frame_width, max_power):
    distance_total = pow(tag.pose_t[0][0],2) + pow(tag.pose_t[1][0],2) + pow(tag.pose_t[2][0],2)
    distance_sqrt = math.sqrt(distance_total)
    forward_speed = int(rescale(distance_sqrt, 0, 3, 0, max_power))
    turning_speed = int(rescale(int(frame_width/2) - tag.center[0], -1 * (frame_width/2), (frame_width/2), -1 * (max_power / 1), (max_power / 1)))
    left_speed = int(forward_speed - turning_speed)
    right_speed = int(forward_speed + turning_speed)
    return left_speed, right_speed


def main(roboclaw):
    vid = cv2.VideoCapture(0)
    at_detector = Detector(families='tag36h11',
                           nthreads=1,
                           quad_decimate=1.0,
                           quad_sigma=0.0,
                           refine_edges=1,
                           decode_sharpening=0.25,
                           debug=0)
    width = int(vid.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(vid.get(cv2.CAP_PROP_FRAME_HEIGHT))

    left_speed = 0
    right_speed = 0
    max_speed = 127
    state = STATE_GO_FARTHEST
    tag_missing_frames = 0
    tag_last_seen = 0

    while (True):

        ret, frame = vid.read()
        gray_image = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        tag_size_cm = 20.5

        focal_length = (1, 1)
        camera_center = (1, 1)

        tags = at_detector.detect(gray_image, estimate_tag_pose=True,
                                  camera_params=[focal_length[0], focal_length[1], camera_center[0], camera_center[1]],
                                  tag_size=(tag_size_cm / 100))

        now = millis()

        # Display tag result info on image
        # From https://pyimagesearch.com/2020/11/02/apriltag-with-python/
        for r in tags:
            # extract the bounding box (x, y)-coordinates for the AprilTag
            # and convert each of the (x, y)-coordinate pairs to integers
            (ptA, ptB, ptC, ptD) = r.corners
            ptB = (int(ptB[0]), int(ptB[1]))
            ptC = (int(ptC[0]), int(ptC[1]))
            ptD = (int(ptD[0]), int(ptD[1]))
            ptA = (int(ptA[0]), int(ptA[1]))
            # draw the bounding box of the AprilTag detection
            cv2.line(frame, ptA, ptB, (0, 255, 0), 2)
            cv2.line(frame, ptB, ptC, (0, 255, 0), 2)
            cv2.line(frame, ptC, ptD, (0, 255, 0), 2)
            cv2.line(frame, ptD, ptA, (0, 255, 0), 2)
            # draw the center (x, y)-coordinates of the AprilTag
            (cX, cY) = (int(r.center[0]), int(r.center[1]))
            cv2.circle(frame, (cX, cY), 5, (0, 0, 255), -1)
            # draw the tag family on the image
            tagFamily = r.tag_family.decode("utf-8")
            cv2.putText(frame, str(r.tag_id), (cX, cY),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        # Find the furthest tag
        farthest_tag = None
        for tag in tags:
            if farthest_tag is None or tag.center[1] < farthest_tag.center[1]:
                farthest_tag = tag

        # Run state machine
        print(chr(27) + "[2J")
        print('State: ' + state)

        if state == STATE_GO_FARTHEST:
            # Adjust power to go to the farthest tag (if one exists)
            if farthest_tag is not None:
                left_speed, right_speed = get_left_right_power_for_tag(farthest_tag, width, max_speed)
            else:
                left_speed = left_speed / 2
                right_speed = right_speed / 2

            # If a turn tag is seen, target that one instead
            for tag in tags:
                if tag.tag_id == TAG_TURN_LEFT:
                    state = STATE_GO_TURN
                    break
        elif state == STATE_GO_TURN:
            turn_tag = None
            for tag in tags:
                if tag.tag_id == TAG_TURN_LEFT:
                    turn_tag = tag
                    break
            if now - tag_last_seen > 3000:
                # Start turning when the turn tag is no longer seen
                state = STATE_TURN_LEFT
            elif turn_tag is not None:
                left_speed, right_speed = get_left_right_power_for_tag(turn_tag, width, max_speed)
            else:
                left_speed = left_speed / 2
                right_speed = right_speed / 2
        elif state == STATE_TURN_LEFT:
            # Keep turning until the furthest non-turning tag is centered
            turning_speed = 30
            left_speed = -1 * turning_speed
            right_speed = turning_speed

            target_tag = None
            for tag in tags:
                if tag.tag_id != TAG_TURN_LEFT and (target_tag is None or tag.center[1] < target_tag.center[1]):
                    target_tag = tag
            if target_tag is not None:
                # Get within x% of center
                center_error_percentage = 15
                distance_from_center = target_tag.center[0] - (width/2)
                distance_percentage = rescale(distance_from_center, -1 * (width/2), (width/2), -100, 100)
                if abs(distance_percentage) < center_error_percentage:
                    left_speed = 0
                    right_speed = 0
                    state = STATE_GO_FARTHEST

        if len(tags) == 0:
            tag_missing_frames = tag_missing_frames + 1
        else:
            tag_missing_frames = 0
            tag_last_seen = now

        print(left_speed)
        print(right_speed)
        if abs(left_speed) < 0.1:
            left_speed = 0
        if abs(right_speed) < 0.1:
            right_speed = 0

        if left_speed > 0:
            roboclaw.ForwardM1(0x80, int(left_speed))
        else:
            roboclaw.BackwardM1(0x80, abs(int(left_speed)))

        if right_speed > 0:
            roboclaw.ForwardM2(0x80, int(right_speed))
        else:
            roboclaw.BackwardM2(0x80, abs(int(right_speed)))

        # Display the resulting frame
        cv2.imshow('Frame', frame)

        # q to quit
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    vid.release()
    cv2.destroyAllWindows()

if __name__ == '__main__':
    roboclaw = Roboclaw('/dev/ttyACM0', 38400)
    roboclaw.Open()
    try:
        main(roboclaw)
    except (Exception, KeyboardInterrupt, SystemExit) as e:
        print(traceback.format_exc())
        print(e)
        print('Interrupted')
        roboclaw.ForwardM1(0x80, 0)
        roboclaw.ForwardM2(0x80, 0)
        try:
            sys.exit(0)
        except SystemExit:
            os._exit(0)
