import matplotlib
matplotlib.use('tkagg')
#matplotlib.use('qt5agg')
from fracPy.ExperimentalData.ExperimentalData import ExperimentalData
from fracPy.Optimizable.Optimizable import Optimizable
from fracPy.engines import ePIE, mPIE, qNewton, zPIE, e3PIE
from fracPy.utils.gpuUtils import getArrayModule, asNumpyArray
from fracPy.monitors.Monitor import Monitor as Monitor
import logging
logging.basicConfig(level=logging.INFO)
from fracPy.utils.utils import ifft2c
from fracPy.utils.visualisation import hsvplot
from matplotlib import pyplot as plt
import numpy as np

""" 
FPM data reconstructor 
change data visualization and initialization options manually for now
"""

FPM_simulation = False
ptycho_simulation = True


if FPM_simulation:
    # create an experimentalData object and load a measurement
    exampleData = ExperimentalData()
    exampleData.loadData('example:simulation_fpm')
    # exampleData.loadData('example:simulation_ptycho')
    exampleData.operationMode = 'FPM'
    # # now, all our experimental data is loaded into experimental_data and we don't have to worry about it anymore.
    # # now create an object to hold everything we're eventually interested in
    optimizable = Optimizable(exampleData)

    optimizable.npsm = 9  # Number of probe modes to reconstruct
    optimizable.nosm = 1  # Number of object modes to reconstruct
    optimizable.nlambda = 1  # Number of wavelength
    optimizable.prepare_reconstruction()
    # # this will copy any attributes from experimental data that we might care to optimize

    # Set monitor properties
    monitor = Monitor()
    monitor.figureUpdateFrequency = 1
    monitor.objectPlot = 'complex'
    monitor.verboseLevel = 'high'

    # now we want to run an optimizer. First create it.
    # qNewton_engine = qNewton.qNewton(optimizable, exampleData, monitor)
    qNewton_engine = qNewton.qNewton_GPU(optimizable, exampleData, monitor)
    # set any settings involving ePIE in this object.
    qNewton_engine.numIterations = 50
    # now, run the reconstruction
    qNewton_engine.doReconstruction()


""" 
ptycho data reconstructor 
change data visualization and initialization options manually for now
"""
if ptycho_simulation:
    exampleData = ExperimentalData()

    import os
    filePath = r"D:\Du\Workshop\fracpy\example_data"
    os.chdir(filePath)

    exampleData.loadData('simuRecent.hdf5')  # simuRecent simuRecent_zPIE Lenspaper

    exampleData.operationMode = 'CPM'

    # now, all our experimental data is loaded into experimental_data and we don't have to worry about it anymore.
    # now create an object to hold everything we're eventually interested in
    optimizable = Optimizable(exampleData)
    optimizable.npsm = 4 # Number of probe modes to reconstruct
    optimizable.nosm = 1 # Number of object modes to reconstruct
    optimizable.nlambda = 1 # Number of wavelength
    optimizable.nslice = 1 # Number of object slice
    exampleData.dz = 1e-4  # slice

    optimizable.initialProbe = 'circ'
    exampleData.entrancePupilDiameter = exampleData.Np / 3 * exampleData.dxp # initial estimate of beam
    optimizable.initialObject = 'ones'
    # initialize probe and object and related params
    optimizable.prepare_reconstruction()
    # customize initial probe quadratic phase
    optimizable.probe = optimizable.probe*np.exp(1.j*2*np.pi/exampleData.wavelength *
                                                 (exampleData.Xp**2+exampleData.Yp**2)/(2*6e-3))
    # hsvplot(np.squeeze(optimizable.probe))

    # this will copy any attributes from experimental data that we might care to optimize
    # # Set monitor properties
    monitor = Monitor()
    monitor.figureUpdateFrequency = 1
    monitor.objectPlot = 'complex'
    monitor.verboseLevel = 'high'  # high: plot two figures, low: plot only one figure
    
    # Run the reconstruction
    ## choose engine
    # ePIE
    engine = ePIE.ePIE_GPU(optimizable, exampleData, monitor)
    # engine = ePIE.ePIE(optimizable, exampleData, monitor)
    # mPIE
    # engine = mPIE.mPIE_GPU(optimizable, exampleData, monitor)
    # engine = mPIE.mPIE(optimizable, exampleData, monitor)
    # zPIE
    # engine = zPIE.zPIE_GPU(optimizable, exampleData, monitor)
    # engine = zPIE.zPIE(optimizable, exampleData, monitor)
    # e3PIE
    # engine = e3PIE.e3PIE_GPU(optimizable, exampleData, monitor)
    # engine = e3PIE.e3PIE(optimizable, exampleData, monitor)

    ## main parameters
    engine.numIterations = 100
    engine.positionOrder = 'random'  # 'sequential' or 'random'
    engine.propagator = 'Fraunhofer'  # Fresnel ASP scaledASP
    engine.betaProbe = 0.25
    engine.betaObject = 0.25

    ## engine specific parameters:
    engine.zPIEgradientStepSize = 1  # gradient step size for axial position correction (typical range [1, 100])

    ## switches
    engine.probePowerCorrectionSwitch = True
    engine.comStabilizationSwitch = True
    engine.orthogonalizationSwitch = True
    engine.orthogonalizationFrequency = 10
    engine.fftshiftSwitch = True
    engine.intensityConstraint = 'standard'  # standard fluctuation exponential poission

    engine.doReconstruction()



    # now save the data
    # optimizable.saveResults('reconstruction.hdf5')
