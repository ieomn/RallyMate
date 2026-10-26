"""Only registered landmark names can establish that a warning is unrelated."""
from rallymate_vision.pose.metadata import load_keypoint_schemas


def known_joint_names(names):
    if not isinstance(names, (list, tuple)) or not names:
        return False
    registered = {point["name"] for schema in load_keypoint_schemas()["formats"].values()
                  for point in schema["keypoints"]}
    return all(isinstance(name, str) and name in registered for name in names)
