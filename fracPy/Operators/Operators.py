import logging
from collections import Callable
from functools import lru_cache

import cupy as cp
import numpy as np

from fracPy.utils.utils import circ, fft2c, ifft2c
from fracPy.utils.gpuUtils import getArrayModule, isGpuArray
from fracPy import Params, Reconstruction

# how many kernels are kept in memory for every type of propagator? Higher can be faster but comes
# at the expense of (GPU) memory.
cache_size=10


def propagate_fraunhofer(fields, params: Params, reconstruction: Reconstruction, z=None):
    return reconstruction.esw, fft2c(fields, params.fftshiftSwitch)

def propagate_fraunhofer_inv(fields, params: Params, reconstruction: Reconstruction, z=None):
    return reconstruction.esw, ifft2c(fields, params.fftshiftSwitch)

def propagate_fresnel(fields, params: Params, reconstruction: Reconstruction, z=None):
    # make the quad phase if it's not available yet
    # print('Running Propagate Fresnel')
    # asldckmasdlkmc
    if z is None:
        z = reconstruction.zo
    on_gpu = isGpuArray(fields)
    quadratic_phase = __make_quad_phase(z, reconstruction.wavelength, fields.shape[-1],
                                        reconstruction.dxp, on_gpu=on_gpu)

    eswUpdate = fft2c(fields*quadratic_phase, params.fftshiftSwitch)
    # for legacy reasons, as far as I can see there's no reason to do this
    #esw = reconstruction.esw * quadratic_phase
    return reconstruction.esw, eswUpdate

def propagate_fresnel_inv(fields, params: Params, reconstruction: Reconstruction, z=None):
    # make the quad phase if it's not available yet
    if z is None:
        z = reconstruction.zo
    quadratic_phase = __make_quad_phase(z, reconstruction.wavelength, reconstruction.Np,
                                        reconstruction.dxp, on_gpu=params.gpuSwitch).conj()

    eswUpdate = ifft2c(fields, params.fftshiftSwitch) * quadratic_phase
    # esw = reconstruction.esw * quadratic_phase
    return reconstruction.esw, eswUpdate

def propagate_ASP(fields, params: Params, reconstruction: Reconstruction, inverse=False, z=None):
    if params.fftshiftSwitch:
        raise ValueError('ASP propagator only works with fftshiftswitch == False')
    if reconstruction.nlambda > 1:
        raise ValueError('For multi-wavelength, set polychromeASP instead of ASP')
    if z is None:
        z = reconstruction.zo
    transfer_function = __make_transferfunction_ASP(params.fftshiftSwitch,
                                                    reconstruction.nosm,
                                                    reconstruction.npsm,
                                                    reconstruction.Np,
                                                    z,
                                                    reconstruction.wavelength,
                                                    reconstruction.Lp,
                                                    reconstruction.nlambda,
                                                    params.gpuSwitch)
    if inverse:
        transfer_function = transfer_function.conj()
    result = ifft2c(fft2c(fields) * transfer_function)
    return reconstruction.esw, result

def propagate_ASP_inv(*args, **kwargs):
    return propagate_ASP(*args, **kwargs, inverse=True)


def propagate_twoStepPolychrome(fields, params: Params, reconstruction: Reconstruction, inverse=False, z=None):
    if z is None:
        z = reconstruction.zo
    transfer_function, quadratic_phase = __make_cache_twoStepPolychrome(params.fftshiftSwitch,
                                                                        reconstruction.nlambda,
                                                                        reconstruction.nosm,
                                                                        reconstruction.npsm,
                                                                        reconstruction.Np,
                                                                        z,
                                                                        # this has to be cast to a tuple to
                                                                        # make sure it is reused
                                                                        tuple(reconstruction.spectralDensity),
                                                                        reconstruction.Lp,
                                                                        reconstruction.dxp,
                                                                        params.gpuSwitch)
    if inverse:
        result = ifft2c(fft2c(fields* quadratic_phase.conj())*transfer_function.conj())
        return reconstruction.esw, result
    else:
        result = ifft2c(fft2c(fields) * transfer_function) * quadratic_phase
        result = fft2c(result, params.fftshiftSwitch)
        return reconstruction.esw, result


def propagate_twoStepPolychrome_inv(fields, params:Params, reconstruction: Reconstruction, z=None):
    F = propagate_twoStepPolychrome(fields, params, reconstruction, inverse=True, z=z)[1]
    G = propagate_twoStepPolychrome(reconstruction.ESW, params, reconstruction, inverse=True, z=z)[1]
    return G, F


def propagate_scaledASP(fields, params: Params, reconstruction: Reconstruction, inverse=False, z=None):
    if z is None:
        z = reconstruction.zo
    Q1, Q2 = __make_transferfunction_scaledASP(params.propagatorType, params.fftshiftSwitch,
                                               reconstruction.nlambda,
                                               reconstruction.nosm,
                                               reconstruction.npsm,
                                               reconstruction.Np,
                                               z,
                                               reconstruction.wavelength,
                                               reconstruction.dxo,
                                               reconstruction.dxd,
                                               params.gpuSwitch)
    if inverse:
        Q1, Q2 = Q1.conj(), Q2.conj()
        return reconstruction.esw, ifft2c(fft2c(fields)*Q2)*Q1
    return reconstruction.esw, ifft2c(fft2c(fields * Q1) * Q2)

def propagate_scaledASP_inv(fields, params: Params, reconstruction: Reconstruction, z=None):
    return propagate_scaledASP(fields, params, reconstruction, inverse=True, z=z)




def propagate_scaledPolychromeASP(fields, params: Params, reconstruction: Reconstruction, inverse=False, z=None):
    if z is None:
        z = reconstruction.zo
    Q1, Q2 = __make_transferfunction_scaledPolychromeASP(params.fftshiftSwitch,
                                                         reconstruction.nlambda,
                                                         reconstruction.nosm,
                                                         reconstruction.npsm,
                                                         z,
                                                         reconstruction.Np,
                                                         tuple(reconstruction.spectralDensity),
                                                         reconstruction.dxo, reconstruction.dxp,
                                                         params.gpuSwitch
                                                         )
    if inverse:
        Q1, Q2 = Q1.conj(), Q2.conj()
        return reconstruction.esw, ifft2c(fft2c(fields) * Q1) * Q2
    return reconstruction.esw, ifft2c(fft2c(fields * Q1) * Q2)

def propagate_scaledPolychromeASP_inv(fields, params: Params, reconstruction: Reconstruction, z=None):
    return propagate_scaledPolychromeASP(fields, params, reconstruction, inverse=True, z=z)

def propagate_polychromeASP(fields, params: Params, reconstruction: Reconstruction, inverse=False, z=None):
    if z is None:
        z = reconstruction.zo
    transfer_function = __make_transferfunction_polychrome_ASP(params.propagatorType,
                                                               params.fftshiftSwitch,
                                                               reconstruction.nosm,
                                                               reconstruction.npsm,
                                                               reconstruction.Np,
                                                               z,
                                                               reconstruction.wavelength,
                                                               reconstruction.Lp,
                                                               reconstruction.nlambda,
                                                               tuple(reconstruction.spectralDensity),
                                                               params.gpuSwitch)

    if inverse:
        transfer_function = transfer_function.conj()
    result = ifft2c(fft2c(fields) * transfer_function)
    return reconstruction.esw, result

def propagate_polychromeASP_inv(fields, params, reconstruction, z=None):
    return  propagate_polychromeASP(fields, params, reconstruction, inverse=True, z=z)



def detector2object(fields, params: Params, reconstruction: Reconstruction):
    """
            Implements detector2object.m. Returns a propagated version of the field.

            If field is not given, reconstruction.esw is taken
            :return: esw, updated esw
            """


    #self.reconstruction.esw = Operators.Operators.object2detector(self.reconstruction.esw, self.params,
    #                                                              self.reconstruction,
    #                                                              )
    # goes to self.reconstruction.ESW
    if fields is None:
        fields = reconstruction.ESW
    method: Callable[np.ndarray, Params, Reconstruction] = reverse_lookup_dictionary[params.propagatorType]
    return method(fields, params, reconstruction)


    # if params.propagatorType == 'Fraunhofer':
    #     return reconstruction.esw, ifft2c(fields, params.fftshiftSwitch)
    # elif params.propagatorType == 'Fresnel':
    #     esw, eswUpdate = _propagate_fresnel_inv(fields, params, reconstruction)
        #
        # # update three things
        # eswUpdate = ifft2c(fields, params.fftshiftSwitch) * reconstruction.quadraticPhase.conj()
        # esw = reconstruction.esw * reconstruction.quadraticPhase.conj()
        # return esw, eswUpdate
    # elif params.propagatorType == 'ASP' or params.propagatorType == 'polychromeASP':
    #     return  reconstruction.esw, ifft2c(fft2c(fields) * reconstruction.transferFunction.conj())
    # elif params.propagatorType == 'scaledASP' or params.propagatorType == 'scaledPolychromeASP':
    #     return reconstruction.esw, ifft2c(
    #         fft2c(fields) * reconstruction.Q2.conj()) * reconstruction.Q1.conj()
    # elif params.propagatorType == 'twoStepPolychrome':
    #     eswUpdate = ifft2c(fields, params.fftshiftSwitch)
    #     esw = ifft2c(fft2c(reconstruction.esw * reconstruction.quadraticPhase.conj())*
    #         reconstruction.transferFunction.conj())
    #     eswUpdate = ifft2c(fft2c(eswUpdate* reconstruction.quadraticPhase.conj()) *
    #                        self.reconstruction.transferFunction.conj())
    #     return esw, eswUpdate
    # else:
    #     raise Exception('Propagator is not properly set, choose from Fraunhofer, Fresnel, ASP and scaledASP')


def object2detector(fields, params: Params, reconstruction: Reconstruction):
    """ Propagate a field from the object to the detector. Return the new object, do not update in-place.
    """


    method: Callable[np.ndarray, Params, Reconstruction] = forward_lookup_dictionary[params.propagatorType]
    if fields is None:
        fields = reconstruction.esw
    return method(fields, params, reconstruction)
    # if params.propagatorType == 'Fraunhofer':
    #     esw = fft2c(fields, params.fftshiftSwitch)
    #     return esw
    #
    # elif params.propagatorType == 'Fresnel':
    #     fields *= reconstruction.quadraticPhase
    #     return fft2c(fields, params.fftshiftSwitch)
    # elif params.propagatorType == 'ASP' or params.propagatorType == 'polychromeASP':
    #     return ifft2c(fft2c(fields) * reconstruction.transferFunction)
    # elif params.propagatorType == 'scaledASP' or params.propagatorType == 'scaledPolychromeASP':
    #     return  ifft2c(fft2c(fields * reconstruction.Q1) * reconstruction.Q2)
    # elif params.propagatorType == 'twoStepPolychrome':
    #     X = ifft2c(fft2c(fields) * reconstruction.transferFunction) * reconstruction.quadraticPhase
    #     return fft2c(X, self.params.fftshiftSwitch)
    # else:
    #     raise Exception('Propagator is not properly set, choose from Fraunhofer, Fresnel, ASP and scaledASP')
    #

def aspw(u, z, wavelength, L):
    """
    Angular spectrum plane wave propagation function.
    following: Matsushima et al., "Band-Limited Angular Spectrum Method for Numerical Simulation of Free-Space
    Propagation in Far and Near Fields", Optics Express, 2009
    :param u: a 2D field distribution at z = 0 (u is assumed to be square, i.e. N x N)
    :param z: propagation distance
    :param wavelength: propagation wavelength in meter
    :param L: total size of the field in meter
    :return: U_prop, Q  (field distribution after propagation and the bandlimited transfer function)
    """
    xp = getArrayModule(u)
    k = 2*np.pi/wavelength
    N = u.shape[-1]
    X = np.arange(-N/2, N/2)/L
    Fx, Fy = np.meshgrid(X, X)
    f_max = L/(wavelength*np.sqrt(L**2+4*z**2))
    # note: see the paper above if you are not sure what this bandlimit has to do here
    # W = rect(Fx/(2*f_max)) .* rect(Fy/(2*f_max));
    W = xp.array(circ(Fx, Fy, 2*f_max))
    # note: accounts for circular symmetry of transfer function and imposes bandlimit to avoid sampling issues
    exponent = 1 - (Fx * wavelength) ** 2 - (Fy * wavelength) ** 2
    # take out stuff that cannot exist
    mask = exponent > 0
    # put the out of range values to 0 so the square root can be taken
    exponent = xp.clip(mask, 0, None)
    H = xp.array(mask * np.exp(1.j * k * z * np.sqrt(exponent)))
    U = fft2c(u)
    u = ifft2c(U * H * W)
    return u, H*W

def scaledASP(u, z, wavelength, dx, dq, bandlimit = True, exactSolution = False):
    """
    Angular spectrum propagation with customized grid spacing dq (within Fresnel(or paraxial) approximation)
    :param u: a 2D square input field
    :param z: propagation distance
    :param wavelength: propagation wavelength
    :param dx: grid spacing in original plane (u)
    :param dq: grid spacing in destination plane (Uout)
    :return: propagated field and two quadratic phases

    note: to be analytically correct, add Q3 (see below)
    if only intensities matter, leave it out
    """
    # optical wavenumber
    k = 2 * np.pi / wavelength
    # assume square grid
    N = u.shape[-1]
    # source plane coordinates
    x1 = np.arange(-N // 2, N // 2) * dx
    X1, Y1 = np.meshgrid(x1, x1)
    r1sq = X1**2+Y1**2
    # spatial frequencies(of source plane)
    f = np.arange(-N // 2, N // 2)/ (N * dx)
    FX, FY = np.meshgrid(f, f)
    fsq = FX**2 + FY**2
    # scaling parameter
    m = dq / dx

    # quadratic phase factors
    Q1 = np.exp(1.j * k / 2 * (1 - m) / z * r1sq)
    Q2 = np.exp(-1.j * np.pi**2 * 2 * z / m / k * fsq)

    if bandlimit:
        if m is not 1:
            r1sq_max = wavelength*z/(2*dx*(1-m))
            Wr = np.array(circ(X1, Y1, 2 * r1sq_max))
            Q1 = Q1*Wr

        fsq_max = m/(2*z*wavelength*(1/(N*dx)))
        Wf = np.array(circ(FX, FY, 2 * fsq_max))
        Q2 = Q2*Wf


    if exactSolution: # if only intensities matter, leave it out
        # observation plane coordinates
        x2 = np.arange(-N // 2, N // 2) * dq
        X2, Y2 = np.meshgrid(x2, x2)
        r2sq = X2**2 + Y2**2
        Q3 = np.exp(1.j * k / 2 * (m - 1) / (m * z) * r2sq)
        # compute the propagated field
        Uout = Q3 * ifft2c(Q2 * fft2c(Q1 * u))
        return Uout, Q1, Q2, Q3
    else: # ignore the phase part in the observation plane
        Uout = ifft2c(Q2 * fft2c(Q1 * u))
        return Uout, Q1, Q2



def scaledASPinv(u, z, wavelength, dx, dq):
    """
    :param u:  a 2D square input field
    :param z:   propagation distance
    :param wavelength: wavelength
    :param dx:  grid spacing in original plane (u)
    :param dq:  grid spacing in destination plane (Uout)
    :return: propagated field

    note: to be analytically correct, add Q3 (see below)
    if only intensities matter, leave it out
    """
    # optical wavenumber
    k = 2 * np.pi / wavelength
    # assume square grid
    N = u.shape[-1]
    # source-plane coordinates
    x1 = np.arange(-N / 2, N / 2) * dx
    Y1, X1 = np.meshgrid(x1, x1)
    r1sq = np.square(X1) + np.square(Y1)
    # spatial frequencies(of source plane)
    f = np.arange(-N / 2, N / 2) / (N * dx)
    FX, FY = np.meshgrid(f, f)
    fsq = FX ** 2 + FY ** 2
    # scaling parameter
    m = dq / dx

    # quadratic phase factors
    Q1 = np.exp(1j * k / 2 * (1 - m) / z * r1sq)
    Q2 = np.exp(-1j * 2 * np.pi ** 2 * z / m / k * fsq)
    Uout = np.conj(Q1) * ifft2c(np.conj(Q2) * fft2c(u))

    # x2 = np.arange(-N / 2, N / 2) * dq
    # X2, Y2 = np.meshgrid(x2,x2)
    # r2sq = X2**2 + Y2**2
    # Q3 = np.exp(1.j * k / 2 * (m - 1) / (m * z) * r2sq)
    # # compute the propagated field
    # Uout = np.conj(Q1) * ifft2c(np.conj(Q2) * fft2c(u*np.conj(Q3)))

    return Uout


def fresnelPropagator(u, z, wavelength, L):
    """
    One-step Fresnel propagation, performing Fresnel-Kirchhoff integral.
    :param u:   field distribution at z = 0(u is assumed to be square, i.e.N x N)
    :param z:   propagation distance
    :param wavelength: wavelength
    :param L: total size[m] of the source plane
    :return: propagated field
    """
    xp = getArrayModule(u)

    k = 2 * np.pi /wavelength
    # source coordinates, assuming square grid
    N = u.shape[-1]
    dx = L / N  # source-plane pixel size
    x = xp.arange(-N // 2, N // 2) * dx
    [Y, X] = xp.meshgrid(x,x)

    # observation coordinates
    dq = wavelength *z / L  # observation-plane pixel size
    q = xp.arange(-N // 2, N // 2) * dq
    [Qy, Qx] = xp.meshgrid(q, q)

    # quadratic phase terms
    Q1 = xp.exp(1j * k / (2 * z) * (X**2 + Y**2))  # quadratic phase inside the integral
    Q2 = xp.exp(1j * k / (2 * z) * (Qx**2 + Qy**2))

    # pre-factor
    A = 1/(1j*wavelength*z)

    # Fresnel-Kirchhoff integral
    u_out = A*Q2*fft2c(u*Q1)
    return u_out, dq, Q1, Q2


def clear_cache(logger: logging.Logger=None):
    """ Clear the cache of all cached functions in this module. Use if GPU memory is not available.

    IF logger is available, print some information about the methods being cleared.

    Returns nothing"""
    list_of_methods = [
        __make_quad_phase,
        __make_transferfunction_ASP,
        __make_transferfunction_scaledASP,
        __make_cache_twoStepPolychrome,
        __make_transferfunction_polychrome_ASP,
        __make_transferfunction_scaledPolychromeASP
    ]
    for method in list_of_methods:
        if logger is not None:
            logger.debug(method.cache_info())
            logger.info('clearing cache for %s', method)
        method.cache_clear()



@lru_cache(maxsize=cache_size)
def __make_quad_phase(zo, wavelength, Np, dxp, on_gpu):
    """
    Make a quadratic phase profile corresponding to distance zo at wavelength wl. The result is cached and can be
    called again with almost no time lost.
    :param wavelength:  wavelength in meters
    :param zo:
    :param Np:
    :param dxp:
    :param on_gpu:
    :return:
    """
    if on_gpu:
        xp = cp
    else:
        xp = np

    x_p = xp.linspace(-Np/2, Np/2, np.int(Np))*dxp
    Xp, Yp = np.meshgrid(x_p, x_p)

    quadraticPhase = xp.exp(1.j * xp.pi / (wavelength * zo)
                                  * (Xp ** 2 + Yp ** 2))
    # print(Xp.shape, Yp.shape, quadraticPhase.shape)
    # alsdkcmasldkcm
    return quadraticPhase


@lru_cache(cache_size)
def __make_transferfunction_ASP(fftshiftSwitch, nosm, npsm, Np,
                            zo, wavelength, Lp, nlambda, on_gpu):
    if fftshiftSwitch:
        raise ValueError('ASP propagatorType works only with fftshiftSwitch = False!')
    if nlambda > 1:
        raise ValueError('For multi-wavelength, polychromeASP needs to be used instead of ASP')

    dummy = np.ones((1, nosm, npsm,
                     1, Np, Np), dtype='complex64')
    _transferFunction = np.array(
        [[[[aspw(dummy[nlambda, nosm, npsm, nslice, :, :],
                 zo, wavelength,
                 Lp)[1]
            for nslice in range(1)]
           for npsm in range(npsm)]
          for nosm in range(nosm)]
         for nlambda in range(nlambda)], dtype=np.complex64)
    if on_gpu:
        return cp.array(_transferFunction)
    else:
        return _transferFunction


@lru_cache(cache_size)
def __make_transferfunction_polychrome_ASP(propagatorType, fftshiftSwitch, nosm, npsm, Np, zo, wavelength, Lp, nlambda,
                                           spectralDensity_as_tuple,
                                           gpuSwitch) -> np.ndarray:
    spectralDensity = np.array(spectralDensity_as_tuple)
    if fftshiftSwitch:
        raise ValueError('ASP propagatorType works only with fftshiftSwitch = False!')
    dummy = np.ones((nlambda, nosm, npsm,
                     1, Np, Np), dtype='complex64')
    transferFunction = np.array(
        [[[[aspw(dummy[nlambda, nosm, npsm, nslice, :, :],
                 zo, spectralDensity[nlambda],
                 Lp)[1]
            for nslice in range(1)]
           for npsm in range(npsm)]
          for nosm in range(nosm)]
         for nlambda in range(nlambda)])
    if gpuSwitch:
        return cp.array(transferFunction, dtype=cp.complex64)
    else:
        return transferFunction


@lru_cache(cache_size)
def __make_transferfunction_scaledASP(propagatorType, fftshiftSwitch,
                                      nlambda, nosm, npsm, Np, zo, wavelength,
                                      dxo, dxd, gpuSwitch):
    if fftshiftSwitch:
        raise ValueError('scaledASP propagatorType works only with fftshiftSwitch = False!')
    if nlambda > 1:
        raise ValueError('For multi-wavelength, scaledPolychromeASP needs to be used instead of scaledASP')
    dummy = np.ones((1, nosm, npsm,
                     1, Np, Np), dtype='complex64')
    _Q1 = np.ones_like(dummy)
    _Q2 = np.ones_like(dummy)
    for nosm in range(nosm):
        for npsm in range(npsm):
            _, _Q1[0, nosm, npsm, 0, ...], _Q2[
                0, nosm, npsm, 0, ...] = scaledASP(
                dummy[0, nosm, npsm, 0, :, :], zo, wavelength,
                dxo, dxd)

    if gpuSwitch:
        return cp.array(_Q1, dtype=np.complex64), cp.array(_Q2, dtype=np.complex64)
    return _Q1, _Q2


@lru_cache(cache_size)
def __make_transferfunction_scaledPolychromeASP(fftshiftSwitch, nlambda, nosm, npsm, zo, Np,
                                                spectralDensity_as_tuple,
                                                dxo, dxd, on_gpu):
    spectralDensity = np.array(spectralDensity_as_tuple)
    if fftshiftSwitch:
        raise ValueError('scaledPolychromeASP propagatorType works only with fftshiftSwitch = False!')
    if on_gpu:
        xp = cp
    else:
        xp = np
    dummy = xp.ones((nlambda, nosm, npsm,
                     1, Np, Np), dtype='complex64')
    Q1 = xp.ones_like(dummy)
    Q2 = xp.ones_like(dummy)
    for nlambda in range(nlambda):
        Q1_candidate, Q2_candidate = __make_transferfunction_scaledASP(None,
                                                                     fftshiftSwitch,
                                                                     1,
                                                                     nosm,
                                                                     npsm,
                                                                     Np,
                                                                     zo,
                                                                     spectralDensity[nlambda],
                                                                     dxo,
                                                                     dxd,
                                                                     gpuSwitch=on_gpu
                                                                     )
        Q1[nlambda], Q2[nlambda] = Q1_candidate[0], Q2_candidate[0]
    return Q1, Q2


@lru_cache(cache_size)
def __make_cache_twoStepPolychrome(fftshiftSwitch,
                                   nlambda, nosm, npsm, Np, zo, spectralDensity_as_tuple, Lp,
                                   dxp, on_gpu):
    spectralDensity = np.array(spectralDensity_as_tuple)
    if fftshiftSwitch:
        raise ValueError('twoStepPolychrome propagatorType works only with fftshiftSwitch = False!')
    dummy = np.ones((nlambda, nosm, npsm,
                     1, Np, Np), dtype='complex64')

    transferFunction = np.array(
        [[[[aspw(u=dummy[nlambda, nosm, npsm, nslice, :, :],
                 z= zo * (1 - spectralDensity[0] / spectralDensity[nlambda]),
                 wavelength=spectralDensity[nlambda],
                 L=Lp)[1]
            for nslice in range(1)]
           for npsm in range(npsm)]
          for nosm in range(nosm)]
         for nlambda in range(nlambda)])
    if on_gpu:
        transferFunction = cp.array(transferFunction)

    quadraticPhase = __make_quad_phase(zo, spectralDensity[0], Np, dxp, on_gpu)
    return transferFunction, quadraticPhase

forward_lookup_dictionary = {
        'Fraunhofer': propagate_fraunhofer,
        'Fresnel': propagate_fresnel,
        'ASP': propagate_ASP,
        'polychromeASP': propagate_polychromeASP,
        'scaledASP': propagate_scaledASP,
        'scaledPolychromeASP': propagate_scaledPolychromeASP,
        'twoStepPolychrome': propagate_twoStepPolychrome,
    }


reverse_lookup_dictionary = {
        'Fraunhofer': propagate_fraunhofer_inv,
        'Fresnel': propagate_fresnel_inv,
        'ASP': propagate_ASP_inv,
        'polychromeASP': propagate_polychromeASP_inv,
        'scaledASP': propagate_scaledASP_inv,
        'scaledPolychromeASP': propagate_scaledPolychromeASP_inv,
        'twoStepPolychrome': propagate_twoStepPolychrome_inv,
    }

