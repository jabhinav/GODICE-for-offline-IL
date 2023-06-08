import os

import numpy as np
from gymnasium.utils.ezpickle import EzPickle
from gymnasium_robotics.envs.robot_env import MujocoRobotEnv
from gymnasium_robotics.utils import rotations


# Ensure we get the path separator correct on windows
MODEL_XML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'asset', 'fetch', 'pick_and_place_twoobj.xml')

DEFAULT_CAMERA_CONFIG = {
    "distance": 2.5,
    "azimuth": 132.0,
    "elevation": -14.0,
    "lookat": np.array([1.3, 0.75, 0.55]),
}


def goal_distance(goal_a, goal_b):
    assert goal_a.shape == goal_b.shape
    return np.linalg.norm(goal_a - goal_b, axis=-1)


class FetchEnv(MujocoRobotEnv):
    """Superclass for all Fetch environments. One object PnP uses built-in FetchEnv from gym.envs.robotics
    """

    def __init__(
        self, model_path, n_substeps, gripper_extra_height, block_gripper,
        has_object, target_in_the_air, target_offset, obj_range, target_range,
        distance_threshold, initial_qpos, reward_type, stacking, first_in_place
    ):
        """Initializes a new Fetch environment.

        Args:
            model_path (string): path to the environments XML file
            n_substeps (int): number of substeps the simulation runs on every call to step
            gripper_extra_height (float): additional height above the table when positioning the gripper
            block_gripper (boolean): whether or not the gripper is blocked (i.e. not movable) or not
            has_object (boolean): whether or not the environment has an object
            target_in_the_air (boolean): whether or not the target should be in the air above the table or on the table surface
            target_offset (float or array with 3 elements): offset of the target (goal), added to initially sampled goal
            obj_range (float): range of a uniform distribution for sampling initial object positions
            target_range (float): range of a uniform distribution for sampling a target (goal)
            distance_threshold (float): the threshold after which a goal is considered achieved
            initial_qpos (dict): a dictionary of joint names and values that define the initial configuration
            reward_type ('sparse' or 'dense'): the reward type, i.e. sparse or dense
            stacking: whether to stack goals on top of each other (the implemented logic is such that second object's goal would always be on first object's goal)
            first_in_place: whether to sample first goal at the first object's position
        """
        self.gripper_extra_height = gripper_extra_height
        self.block_gripper = block_gripper
        self.has_object = has_object
        self.target_in_the_air = target_in_the_air
        self.target_offset = target_offset
        self.obj_range = obj_range
        self.target_range = target_range
        self.distance_threshold = distance_threshold
        self.reward_type = reward_type

        self.stacking = stacking
        self.stack_height_offset = 0.05  # TODO: figure out if it's .025 or .05 (0.025 not working, goals too close)
        self.first_in_place = first_in_place
        
        print("Model path: ", model_path)

        super(FetchEnv, self).__init__(
            default_camera_config=DEFAULT_CAMERA_CONFIG,
            model_path=model_path,
            n_substeps=n_substeps,
            n_actions=4,
            initial_qpos=initial_qpos,
            # render_mode="human",  # Uncommenting it will use the in-built rendering (unintended)
        )

    # GoalEnv methods
    # ----------------------------

    def compute_reward(self, achieved_goal, goal, info):
        # Compute distance between goal and the achieved goal.
        d = goal_distance(achieved_goal, goal)
        if self.reward_type == 'sparse':
            return -(d > self.distance_threshold).astype(np.float32)
        else:
            return -d

    # RobotEnv methods
    # ----------------------------

    def _step_callback(self):

        if self.block_gripper:
            self._utils.set_joint_qpos(
                self.model, self.data, "robot0:l_gripper_finger_joint", 0.0
            )
            self._utils.set_joint_qpos(
                self.model, self.data, "robot0:r_gripper_finger_joint", 0.0
            )
            self._mujoco.mj_forward(self.model, self.data)

    def _set_action(self, action):
        assert action.shape == (4,)
        action = (
            action.copy()
        )  # ensure that we don't change the action outside of this scope
        pos_ctrl, gripper_ctrl = action[:3], action[3]

        pos_ctrl *= 0.05  # limit maximum change in position
        rot_ctrl = [
            1.0,
            0.0,
            1.0,
            0.0,
        ]  # fixed rotation of the end effector, expressed as a quaternion
        gripper_ctrl = np.array([gripper_ctrl, gripper_ctrl])
        assert gripper_ctrl.shape == (2,)
        if self.block_gripper:
            gripper_ctrl = np.zeros_like(gripper_ctrl)
        action = np.concatenate([pos_ctrl, rot_ctrl, gripper_ctrl])

        # Apply action to simulation.
        self._utils.ctrl_set_action(self.model, self.data, action)
        self._utils.mocap_set_action(self.model, self.data, action)

    def _get_obs(self):

        grip_pos = self._utils.get_site_xpos(self.model, self.data, "robot0:grip")
        dt = self.n_substeps * self.model.opt.timestep
        grip_velp = (
                self._utils.get_site_xvelp(self.model, self.data, "robot0:grip") * dt
        )

        robot_qpos, robot_qvel = self._utils.robot_get_obs(
            self.model, self.data, self._model_names.joint_names
        )
        if self.has_object:
            object1_pos = self._utils.get_site_xpos(self.model, self.data, "object0")
            object2_pos = self._utils.get_site_xpos(self.model, self.data, "object1")

            # rotations
            object1_rot = rotations.mat2euler(
                self._utils.get_site_xmat(self.model, self.data, "object0")
            )
            object2_rot = rotations.mat2euler(
                self._utils.get_site_xmat(self.model, self.data, "object1")
            )

            # velocities
            object1_velp = (
                    self._utils.get_site_xvelp(self.model, self.data, "object0") * dt
            )
            object1_velr = (
                    self._utils.get_site_xvelr(self.model, self.data, "object0") * dt
            )
            object2_velp = (
                    self._utils.get_site_xvelp(self.model, self.data, "object1") * dt
            )
            object2_velr = (
                    self._utils.get_site_xvelr(self.model, self.data, "object1") * dt
            )

            # gripper state
            object1_rel_pos = object1_pos - grip_pos
            object1_velp -= grip_velp
            object2_rel_pos = object2_pos - grip_pos
            object2_velp -= grip_velp
        else:
            object1_pos = object1_rot = object1_velp = object1_velr = object1_rel_pos = np.zeros(0)
            object2_pos = object2_rot = object2_velp = object2_velr = object2_rel_pos = np.zeros(0)

        gripper_state = robot_qpos[-2:]

        gripper_vel = (
                robot_qvel[-2:] * dt
        )  # change to a scalar if the gripper is made symmetric

        if not self.has_object:
            achieved_goal = grip_pos.copy()
        else:
            achieved_goal = np.concatenate([np.squeeze(object1_pos.copy()), np.squeeze(object2_pos.copy())])
        obs = np.concatenate([
            grip_pos,
            object1_pos.ravel(),
            object2_pos.ravel(),
            object1_rel_pos.ravel(),
            object2_rel_pos.ravel(),
            gripper_state,
            object1_rot.ravel(),
            object1_velp.ravel(),
            object1_velr.ravel(),
            object2_rot.ravel(),
            object2_velp.ravel(),
            object2_velr.ravel(),
            grip_velp,
            gripper_vel,
        ])

        return {
            'observation': obs.copy(),
            'achieved_goal': achieved_goal.copy(),
            'desired_goal': self.goal.copy(),
        }

    def _get_gripper_xpos(self):
        body_id = self._model_names.body_name2id["robot0:gripper_link"]
        return self.data.xpos[body_id]

    def _render_callback(self):

        # Visualize target.
        sites_offset = (self.data.site_xpos - self.model.site_pos).copy()
        site_id = self._mujoco.mj_name2id(
            self.model, self._mujoco.mjtObj.mjOBJ_SITE, "target0"
        )
        site_id1 = self._mujoco.mj_name2id(
            self.model, self._mujoco.mjtObj.mjOBJ_SITE, "target1"
        )
        self.model.site_pos[site_id] = self.goal[:3] - sites_offset[0]
        self.model.site_pos[site_id1] = self.goal[3:6] - sites_offset[0]
        self._mujoco.mj_forward(self.model, self.data)

    def _reset_sim(self):

        self.data.time = self.initial_time
        self.data.qpos[:] = np.copy(self.initial_qpos)
        self.data.qvel[:] = np.copy(self.initial_qvel)
        if self.model.na != 0:
            self.data.act[:] = None

        # Randomize start position of object.
        if self.has_object:
            object_xpos = object_xpos1 = self.initial_gripper_xpos[:2]
            while np.linalg.norm(object_xpos - self.initial_gripper_xpos[:2]) < 0.1 or np.linalg.norm(object_xpos1 - self.initial_gripper_xpos[:2]) < 0.1:
                object_xpos = self.initial_gripper_xpos[:2] + self.np_random.uniform(
                    -self.obj_range, self.obj_range, size=2
                )
                object_xpos1 = self.initial_gripper_xpos[:2] + self.np_random.uniform(
                    -self.obj_range, self.obj_range, size=2
                )
            object_qpos = self._utils.get_joint_qpos(self.model, self.data, "object0:joint")
            object_qpos1 = self._utils.get_joint_qpos(self.model, self.data, "object1:joint")
            assert object_qpos.shape == (7,)
            object_qpos[:2] = object_xpos
            object_qpos1[:2] = object_xpos1
            self._utils.set_joint_qpos(self.model, self.data, "object0:joint", object_qpos)
            self._utils.set_joint_qpos(self.model, self.data, "object1:joint", object_qpos1)

        self._mujoco.mj_forward(self.model, self.data)
        return True

    def forced_reset(self, state_dict):
    
        # # Set State
        # initial_state = state_dict['state']
        # self.sim.set_state(initial_state)
        # self.sim.forward()
        #
        # # Set Goal
        # self.goal = state_dict['goal']
        # obs = self._get_obs()
        # return obs
        raise NotImplementedError

    def get_state_dict(self):
        # state = self.sim.get_state()
        # goal = self.goal
        # return {
        #     'state': state,
        #     'goal': goal
        # }
        raise NotImplementedError

    def _sample_goal(self):

        if self.has_object:

            object_qpos = self._utils.get_joint_qpos(self.model, self.data, "object0:joint")[:3]
            object_qpos1 = self._utils.get_joint_qpos(self.model, self.data, "object1:joint")[:3]

            # the first object is always on the table
            if not self.first_in_place:
                goal0 = self.initial_gripper_xpos[:3] + self.np_random.uniform(
                    -self.target_range, self.target_range, size=3
                )
                goal0 += self.target_offset
                goal0[2] = self.height_offset

                # ---------------- Cond 1: To make goal0 sufficiently far from objects --------------------------------
                while np.linalg.norm(goal0 - object_qpos) < 0.1 or np.linalg.norm(goal0 - object_qpos1) < 0.1:

                    # print("Cond1: resampling the goal0")
                    goal0 = self.initial_gripper_xpos[:3] + self.np_random.uniform(-self.target_range,
                                                                                   self.target_range,
                                                                                   size=3)
                    goal0 += self.target_offset
                    goal0[2] = self.height_offset

            else:
                goal0 = self._utils.get_site_xpos(self.model, self.data,  'object0')  # first block is already in place!

            if not self.stacking:
                goal1 = self.initial_gripper_xpos[:3] + self.np_random.uniform(
                    -self.target_range, self.target_range, size=3
                )
                goal1 += self.target_offset
                goal1[2] = self.height_offset

                # ----------- Cond 2: To make the second goal sufficiently far from first goal and objects ------------
                while np.linalg.norm(goal0 - goal1) < 0.1 or \
                        np.linalg.norm(goal1 - object_qpos) < 0.1 or \
                        np.linalg.norm(goal1 - object_qpos1) < 0.1:

                    # print("Cond2: resampling the goal1")
                    goal1 = self.initial_gripper_xpos[:3] + self.np_random.uniform(
                        -self.target_range, self.target_range, size=3
                    )
                    goal1 += self.target_offset
                    goal1[2] = self.height_offset

                # if target_in_the_air and with 0.5 selection prob, inc. z_pos of the goal1
                if self.target_in_the_air and self.np_random.uniform() < 0.5:
                    goal1[2] += self.np_random.uniform(0, 0.45)

            # [HERE] Set goals for stacking
            else:
                goal1 = np.copy(goal0)
                goal1[2] = goal0[2] + self.stack_height_offset

            goal = np.concatenate([goal0, goal1])
        else:
            goal0 = self.initial_gripper_xpos[:3] + self.np_random.uniform(
                -self.target_range, self.target_range, size=3
            )
            goal1 = self.initial_gripper_xpos[:3] + self.np_random.uniform(
                -self.target_range, self.target_range, size=3
            )
            goal = np.concatenate([goal0, goal1])

        return goal.copy()

    def _is_success(self, achieved_goal, desired_goal):
        d = goal_distance(achieved_goal, desired_goal)
        return (d < self.distance_threshold).astype(np.float32)

    def _env_setup(self, initial_qpos):

        for name, value in initial_qpos.items():
            self._utils.set_joint_qpos(self.model, self.data, name, value)
        self._utils.reset_mocap_welds(self.model, self.data)
        self._mujoco.mj_forward(self.model, self.data)

        # Move end effector into position.
        gripper_target = np.array(
            [-0.498, 0.005, -0.431 + self.gripper_extra_height]
        ) + self._utils.get_site_xpos(self.model, self.data, "robot0:grip")
        gripper_rotation = np.array([1.0, 0.0, 1.0, 0.0])
        self._utils.set_mocap_pos(self.model, self.data, "robot0:mocap", gripper_target)
        self._utils.set_mocap_quat(self.model, self.data, "robot0:mocap", gripper_rotation)
        for _ in range(10):
            self._mujoco.mj_step(self.model, self.data, nstep=self.n_substeps)

        # Extract information for sampling goals.
        self.initial_gripper_xpos = self._utils.get_site_xpos(self.model, self.data, "robot0:grip").copy()
        if self.has_object:
            self.height_offset = self._utils.get_site_xpos(self.model, self.data, "object0")[2]

    def render(self, mode="human", width=500, height=500):
        """Overrides the render method to support rendering with control."""
        self._render_callback()
        return self.mujoco_renderer.render(mode)


class FetchPickAndPlaceEnv(FetchEnv, EzPickle):
    def __init__(self, reward_type='sparse', stacking=False, first_in_place=False, distance_threshold=0.01):

        # Define the initial positions of all the env elements. The object's pos will be later changed
        initial_qpos = {
            'robot0:slide0': 0.405,
            'robot0:slide1': 0.48,
            'robot0:slide2': 0.0,
            'object0:joint': [1.25, 0.53, 0.4, 1., 0., 0., 0.],
            'object1:joint': [1.25, 0.53, 0.4, 1., 0., 0., 0.],
        }
        FetchEnv.__init__(
            self, MODEL_XML_PATH, has_object=True, block_gripper=False, n_substeps=20,
            gripper_extra_height=0.2, target_in_the_air=False, target_offset=0.0,
            obj_range=0.15, target_range=0.15, distance_threshold=distance_threshold,
            initial_qpos=initial_qpos, reward_type=reward_type, stacking=stacking, first_in_place=first_in_place)
        EzPickle.__init__(self)