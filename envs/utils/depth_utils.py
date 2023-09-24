# Copyright 2016 The TensorFlow Authors All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

"""Utilities for processing depth images.
"""
from argparse import Namespace

import itertools
import mindspore as ms
import mindspore.numpy as np
from mindspore import Tensor, ops

import envs.utils.rotation_utils as ru


meshgrid = ops.Meshgrid(indexing="xy")
stack = ops.Stack(axis=-1)
floor = ops.Floor()


def get_camera_matrix(width, height, fov):
    """Returns a camera matrix from image size and fov."""
    xc = (width - 1.) / 2.
    zc = (height - 1.) / 2.
    f = (width / 2.) / np.tan(np.deg2rad(ms.Tensor(fov / 2.)))
    camera_matrix = {'xc': xc, 'zc': zc, 'f': f}
    camera_matrix = Namespace(**camera_matrix)
    return camera_matrix


def get_camera_intrinsic_parameters(width, height, hfov):
    """Returns a camera matrix from image size and fov."""
    aspect_ratio = 1.0 * width / height
    temp = np.tan(np.deg2rad(ms.Tensor(hfov / 2.)))

    cx = width / 2.
    cy = height / 2.
    fx = (width / 2.) / temp

    aspect_ratio = 1.0 * width / height
    # vfov = 2 * np.arctan(temp / aspect_ratio)
    # fy = (height / 2.) / np.tan(vfov / 2.)
    fy = (height / 2.) / (temp / aspect_ratio)
    camera_matrix = {'cx': cx, 'cy': cy, 'fx': fx, 'fy': fy,
                     'xc': (width - 1.) / 2., 'zc': (height - 1.) / 2., 'f': fx}
    camera_matrix = Namespace(**camera_matrix)
    return camera_matrix


def get_point_cloud_from_z(Y, camera_matrix, scale=1):
    """Projects the depth image Y into a 3D point cloud.
    Inputs:
        Y is ...xHxW
        camera_matrix
    Outputs:
        X is positive going right
        Y is positive into the image
        Z is positive up in the image
        XYZ is ...xHxWx3
    """
    x, z = meshgrid((np.arange(Y.shape[-1]), np.arange(Y.shape[-2] - 1, -1, -1)))
    for _ in range(Y.ndim - 2):
        x = np.expand_dims(x, axis=0)
        z = np.expand_dims(z, axis=0)
    X = (x[::scale, ::scale] - camera_matrix.xc) * \
        Y[::scale, ::scale] / camera_matrix.f
    Z = (z[::scale, ::scale] - camera_matrix.zc) * \
        Y[::scale, ::scale] / camera_matrix.f
    XYZ = np.concatenate((X[..., np.newaxis],
                          Y[::scale, ::scale][..., np.newaxis],
                          Z[..., np.newaxis]), axis=X.ndim)
    return XYZ


def transform_camera_view(XYZ, sensor_height, camera_elevation_degree):
    """
    Transforms the point cloud into geocentric frame to account for
    camera elevation and angle
    Input:
        XYZ                     : ...x3
        sensor_height           : height of the sensor
        camera_elevation_degree : camera elevation to rectify.
    Output:
        XYZ : ...x3
    """
    R = ru.get_r_matrix(
        [1., 0., 0.], angle=np.deg2rad(ms.Tensor(camera_elevation_degree)))
    XYZ = np.matmul(XYZ.reshape(-1, 3), R.T).reshape(XYZ.shape)
    XYZ[..., 2] = XYZ[..., 2] + sensor_height
    return XYZ


def transform_pose(XYZ, current_pose):
    """
    Transforms the point cloud into geocentric frame to account for
    camera position
    Input:
        XYZ                     : ...x3
        current_pose            : camera position (x, y, theta (radians))
    Output:
        XYZ : ...x3
    """
    R = ru.get_r_matrix([0., 0., 1.], angle=current_pose[2] - np.pi / 2.)
    XYZ = np.matmul(XYZ.reshape(-1, 3), R.T).reshape(XYZ.shape)
    XYZ[:, :, 0] = XYZ[:, :, 0] + current_pose[0]
    XYZ[:, :, 1] = XYZ[:, :, 1] + current_pose[1]
    return XYZ


def bin_points(XYZ_cms, map_size, z_bins, xy_resolution):
    """Bins points into xy-z bins
    XYZ_cms is ... x H x W x3
    Outputs is ... x map_size x map_size x (len(z_bins)+1)
    """
    sh = XYZ_cms.shape
    XYZ_cms = XYZ_cms.reshape([-1, sh[-3], sh[-2], sh[-1]])
    n_z_bins = len(z_bins) + 1
    counts = []
    for XYZ_cm in XYZ_cms:
        isnotnan = np.logical_not(np.isnan(XYZ_cm[:, :, 0]))
        X_bin = np.round(XYZ_cm[:, :, 0] / xy_resolution).astype(np.int32)
        Y_bin = np.round(XYZ_cm[:, :, 1] / xy_resolution).astype(np.int32)
        Z_bin = np.digitize(XYZ_cm[:, :, 2], bins=z_bins).astype(np.int32)

        isvalid = np.array([X_bin >= 0, X_bin < map_size, Y_bin >= 0,
                            Y_bin < map_size,
                            Z_bin >= 0, Z_bin < n_z_bins, isnotnan])
        isvalid = np.all(isvalid, axis=0)

        ind = (Y_bin * map_size + X_bin) * n_z_bins + Z_bin
        ind[np.logical_not(isvalid)] = 0
        count = np.bincount(ind.ravel(), isvalid.ravel().astype(np.int32),
                            minlength=map_size * map_size * n_z_bins)
        counts = np.reshape(count, [map_size, map_size, n_z_bins])

    counts = counts.reshape(list(sh[:-3]) + [map_size, map_size, n_z_bins])

    return counts


def get_point_cloud_from_z_t(Y_t, camera_matrix, scale=1):
    """Projects the depth image Y into a 3D point cloud.
    Inputs:
        Y is ...xHxW
        camera_matrix
    Outputs:
        X is positive going right
        Y is positive into the image
        Z is positive up in the image
        XYZ is ...xHxWx3
    """
    grid_x, grid_z = meshgrid((np.arange(Y_t.shape[-1]),
                                    np.arange(Y_t.shape[-2] - 1, -1, -1)))
    broadcast_to = ops.BroadcastTo(Y_t.shape)
    grid_x = broadcast_to(grid_x.unsqueeze(0))
    grid_z = broadcast_to(grid_z.unsqueeze(0))

    X_t = (grid_x[:, ::scale, ::scale] - camera_matrix.xc) * \
        Y_t[:, ::scale, ::scale] / camera_matrix.f
    Z_t = (grid_z[:, ::scale, ::scale] - camera_matrix.zc) * \
        Y_t[:, ::scale, ::scale] / camera_matrix.f

    XYZ = stack(
        [X_t, Y_t[:, ::scale, ::scale], Z_t])

    return XYZ


def transform_camera_view_t(
        XYZ, sensor_height, camera_elevation_degree):
    """
    Transforms the point cloud into geocentric frame to account for
    camera elevation and angle
    Input:
        XYZ                     : ...x3
        sensor_height           : height of the sensor
        camera_elevation_degree : camera elevation to rectify.
    Output:
        XYZ : ...x3
    """
    R = ru.get_r_matrix(
        [1., 0., 0.], angle=np.deg2rad(ms.Tensor(camera_elevation_degree)))
    XYZ = ops.matmul(XYZ.reshape(-1, 3).float(),
                       Tensor.from_numpy(R).float().transpose(1, 0)
                       ).reshape(XYZ.shape)
    XYZ[..., 2] = XYZ[..., 2] + sensor_height
    return XYZ


def transform_pose_t(XYZ, current_pose):
    """
    Transforms the point cloud into geocentric frame to account for
    camera position
    Input:
        XYZ                     : ...x3
        current_pose            : camera position (x, y, theta (radians))
    Output:
        XYZ : ...x3
    """
    R = ru.get_r_matrix([0., 0., 1.], angle=current_pose[2] - np.pi / 2.)
    XYZ = ops.matmul(XYZ.reshape(-1, 3),
                       Tensor.from_numpy(R).float().transpose(1, 0)
                       ).reshape(XYZ.shape)
    XYZ[..., 0] += current_pose[0]
    XYZ[..., 1] += current_pose[1]
    return XYZ


def depth_image_to_point_cloud(depth, scale, cx, cy, fx, fy, matrix):
    device = depth.device
    bs, h, w = depth.shape

    u = ops.arange(0, w)
    v = ops.arange(0, h)

    v, u = meshgrid((v, u))
    u = u.float().cuda(device)
    v = v.float().cuda(device)
    u = u.unsqueeze(0).repeat(bs, 1, 1)
    v = v.unsqueeze(0).repeat(bs, 1, 1)

    Z = depth / scale
    X = (u - cx) * Z / fx
    Y = (v - cy) * Z / fy

    X = X.flatten()
    Y = Y.flatten()
    Z = Z.flatten()

    valid = Z > 0

    X = X[valid]
    Y = Y[valid]
    Z = Z[valid]

    position = ops.vstack((X, Y, Z, ops.ones(len(X)).cuda(device))).view(bs, 4, -1)
    position = ops.bmm(matrix.cuda(device).unsqueeze(0).repeat(bs, 1, 1), position)

    points = position[:, 0:3, :].view(bs, 3, h, w).permute(0, 2, 3, 1)

    return points


def splat_feat_nd(init_grid, feat, coords):
    """
    Args:
        init_grid: B X nF X W X H X D X ..
        feat: B X nF X nPt
        coords: B X nDims X nPt in [-1, 1]
    Returns:
        grid: B X nF X W X H X D X ..
    """
    wts_dim = []
    pos_dim = []
    grid_dims = init_grid.shape[2:]

    B = init_grid.shape[0]
    F = init_grid.shape[1]

    n_dims = len(grid_dims)

    grid_flat = init_grid.view(B, F, -1)

    for d in range(n_dims):
        pos = coords[:, [d], :] * grid_dims[d] / 2 + grid_dims[d] / 2
        pos_d = []
        wts_d = []

        for ix in [0, 1]:
            pos_ix = floor(pos) + ix
            safe_ix = (pos_ix > 0) & (pos_ix < grid_dims[d])
            safe_ix = safe_ix.float()

            wts_ix = 1 - ops.abs(pos - pos_ix)

            wts_ix = wts_ix * safe_ix
            pos_ix = pos_ix * safe_ix

            pos_d.append(pos_ix)
            wts_d.append(wts_ix)

        pos_dim.append(pos_d)
        wts_dim.append(wts_d)

    l_ix = [[0, 1] for d in range(n_dims)]

    for ix_d in itertools.product(*l_ix):
        wts = ops.ones_like(wts_dim[0][0])
        index = ops.zeros_like(wts_dim[0][0])
        for d in range(n_dims):
            index = index * grid_dims[d] + pos_dim[d][ix_d[d]]
            wts = wts * wts_dim[d][ix_d[d]]

        index = index.long()
        # broadcast_to = ops.BroadcastTo((index.shape[0], F, index.shape[2]))
        # index = broadcast_to(index)
        # scatter_nd_add = ops.ScatterNdAdd()
        # grid_flat = scatter_nd_add(grid_flat, index, feat * wts)
        updates = feat * wts
        assert index.shape[0] == 1
        grid_flat[..., index[0, 0, :]] += updates
        grid_flat = ops.round(grid_flat)

    return grid_flat.view(init_grid.shape)
