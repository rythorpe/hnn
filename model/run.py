#!/usr/bin/env python
# run.py - primary run function for s1 project
#
# v 1.10.0-py35
# rev 2016-05-01 (SL: removed izip, fixed an nhost bug)
# last major: (SL: toward python3)
# other branch for hnn

import os
import sys
import time
import shutil
import numpy as np
from neuron import h
h.load_file("stdrun.hoc")
# Cells are defined in other files
import network
import fileio as fio
import paramrw as paramrw
import plotfn as plotfn
import specfn as specfn

# spike write function
def spikes_write (net, filename_spikes):
  pc = h.ParallelContext()
  for rank in range(int(pc.nhost())):
    # guarantees node order and no competition
    pc.barrier()
    if rank == int(pc.id()):
      # net.spiketimes and net.spikegids are type h.Vector()
      L = int(net.spikegids.size())
      with open(filename_spikes, 'a') as file_spikes:
        for i in range(L):
          file_spikes.write('%3.2f\t%d\n' % (net.spiketimes.x[i], net.spikegids.x[i]))
  # let all nodes iterate through loop in which only one rank writes
  pc.barrier()

# copies param file into root dsim directory
def copy_paramfile (dsim, f_psim, str_date):
  # assumes in this cwd, can use try/except in the future
  print(os.path.join(os.getcwd(), f_psim))
  paramfile = f_psim.split("/")[-1]
  paramfile_orig = os.path.join(os.getcwd(), f_psim)
  paramfile_sim = os.path.join(dsim, paramfile)
  shutil.copyfile(paramfile_orig, paramfile_sim)
  # open the new param file and append the date to it
  with open(paramfile_sim, 'a') as f_param: f_param.write('\nRun_Date: %s' % str_date)

# callback function for printing out time during simulation run
printdt = 10
def prsimtime ():
  sys.stdout.write('\rSimulation time: {0} ms...'.format(round(h.t,2)))
  sys.stdout.flush()

def savedat (p,dproj,f_psim,rank,doutf,t_vec,dp_rec_L2,dp_rec_L5,net,t_sims,debug,exp_prefix,t_trial_start,simindex):
  # write time and calculated dipole to data file only if on the first proc
  # only execute this statement on one proc
  if rank == 0:
    # write the dipole
    with open(doutf['file_dpl'], 'a') as f:
      for k in range(int(t_vec.size())):
        f.write("%03.3f\t" % t_vec.x[k])
        f.write("%5.4f\t" % (dp_rec_L2.x[k] + dp_rec_L5.x[k]))
        f.write("%5.4f\t" % dp_rec_L2.x[k])
        f.write("%5.4f\n" % dp_rec_L5.x[k])
    # write the somatic current to the file
    # for now does not write the total but just L2 somatic and L5 somatic
    with open(doutf['file_current'], 'w') as fc:
      for t, i_L2, i_L5 in zip(t_vec.x, net.current['L2Pyr_soma'].x, net.current['L5Pyr_soma'].x):
        fc.write("%03.3f\t" % t)
        # fc.write("%5.4f\t" % (i_L2 + i_L5))
        fc.write("%5.4f\t" % i_L2)
        fc.write("%5.4f\n" % i_L5)
    # write the params, but add some more information
    p['exp_prefix'] = exp_prefix
    # write params to the file
    paramrw.write(doutf['file_param'], p, net.gid_dict)
    if debug:
      with open(doutf['filename_debug'], 'w+') as file_debug:
        for m in range(int(t_vec.size())):
          file_debug.write("%03.3f\t%5.4f\n" % (t_vec.x[m], v_debug.x[m]))
      # also create a debug plot
      pdipole(doutf['filename_debug'], os.getcwd())
  # write output spikes
  file_spikes_tmp = fio.file_spike_tmp(dproj)
  spikes_write(net, file_spikes_tmp)
  # move the spike file to the spike dir
  if rank == 0:
    shutil.move(file_spikes_tmp, doutf['file_spikes'])
    t_sims[simindex] = time.time() - t_trial_start
    print("... finished in: %4.4f s" % (t_sims[simindex]))

def runanalysis (ddir,p):
  print("Analysis ...",)
  t_start_analysis = time.time()
  # run the spectral analysis
  spec_opts = {
    'type': 'dpl_laminar',
    'f_max': p['f_max_spec'],
    'save_date': 0,
    'runtype': 'parallel',
  }
  specfn.analysis_typespecific(ddir, spec_opts)
  print("time: %4.4f s" % (time.time() - t_start_analysis))

def savefigs (ddir,p,p_exp):
  print("Plot ...",)
  plot_start = time.time()
  # run plots and epscompress function
  # spec results is passed as an argument here
  # because it's not necessarily saved
  xlim_plot = (0., p['tstop'])
  plotfn.pall(ddir, p_exp, xlim_plot)
  print("time: %4.4f s" % (time.time() - plot_start))

def setupsimdir (f_psim,dproj,p_exp):
  ddir = fio.SimulationPaths()
  ddir.create_new_sim(dproj, p_exp.expmt_groups, p_exp.sim_prefix)
  #ddir.create_dirs()
  copy_paramfile(ddir.dsim, f_psim, ddir.str_date)
  # iterate through groups and through params in the group
  N_expmt_groups = len(p_exp.expmt_groups)
  s = '%i total experimental group'
  # purely for vanity
  if N_expmt_groups > 1: s += 's'
  print(s % N_expmt_groups)
  return ddir

def setoutfiles (ddir,expmt_group,exp_prefix,debug):
  doutf = {}
  # create file names
  doutf['file_dpl'] = ddir.create_filename(expmt_group, 'rawdpl', exp_prefix)
  doutf['file_current'] = ddir.create_filename(expmt_group, 'rawcurrent', exp_prefix)
  doutf['file_param'] = ddir.create_filename(expmt_group, 'param', exp_prefix)
  doutf['file_spikes'] = ddir.create_filename(expmt_group, 'rawspk', exp_prefix)
  doutf['file_spec'] = ddir.create_filename(expmt_group, 'rawspec', exp_prefix)
  # if debug is set to 1, this debug block will run
  if debug:
    # net's method rec_debug(rank, gid)
    v_debug = net.rec_debug(0, 8)
  else:
    v_debug = None
  doutf['filename_debug'] = 'debug.dat'
  return doutf, v_debug

# All units for time: ms
def runsim (f_psim):
  # clock start time
  t0 = time.time()
  # dealing with multiple params - there is a lot of overhead to this
  # read the ranges of params and make up all combinations
  # for loop that changes these params serially, with different file names and whatnot
  # serial execution of each param file, since we're already doing charity here
  # copy the param file and write the param dict to a file for that specific sim.
  pc = h.ParallelContext()
  rank = int(pc.id())
  # print(rank, pc.nhost())
  # creates p_exp.sim_prefix and other param structures
  p_exp = paramrw.ExpParams(f_psim)
  # project directory
  dproj = fio.return_data_dir()
  if rank == 0: ddir = setupsimdir(f_psim,dproj,p_exp) # one directory for all experiments
  # core iterator through experimental groups
  expmt_group = p_exp.expmt_groups[0]

  # simulation times, to get a qnd avg
  t_sims = np.zeros(1)
  # iterate through number of unique simulations
  if rank == 0: t_expmt_start = time.time()
  # return the param dict for this simulation
  p = p_exp.return_pdict(expmt_group, 0)
  # get all nodes to this place before continuing
  # tries to ensure we're all running the same params at the same time!
  pc.barrier()
  pc.gid_clear()

  # create a compound index for all sims
  simindex = n = 0
  # trial start time
  t_trial_start = time.time()

  # global variables, should be node-independent
  h("dp_total_L2 = 0."); h("dp_total_L5 = 0.")

  # if there are N_trials, then randomize the seed
  # establishes random seed for the seed seeder (yeah.)
  # this creates a prng_tmp on each, but only the value from 0 will be used
  prng_tmp = np.random.RandomState()
  if rank == 0:
    # initialize vector to 1 element, with a 0
    # v = h.Vector(Length, Init)
    r = h.Vector(1, 0)
    # seeds that come from prng_base are stereotyped
    # these are seeded with seed rank! Blerg.
    if not p_exp.N_trials:
      prng_base = np.random.RandomState(rank)
    else:
      # Create a random seed value
      r.x[0] = prng_tmp.randint(1e9)
  else:
    # create the vector 'r' but don't change its init value
    r = h.Vector(1, 0)

  # broadcast random seed value in r to everyone
  pc.broadcast(r, 0)
  # set object prngbase to random state for the seed value
  # other random seeds here will then be based on the gid
  prng_base = np.random.RandomState(int(r.x[0]))
  # seed list is now a list of seeds to be changed on each run
  # otherwise, its originally set value will remain
  # give a random int seed from [0, 1e9]
  for param in p_exp.prng_seed_list: p[param] = prng_base.randint(1e9)

  # Set tstop before instantiating any classes
  h.tstop = p['tstop']; h.dt = p['dt'] # simulation duration and time-step
  # create prefix for files everyone knows about
  exp_prefix = p_exp.trial_prefix_str % (i, j)
  # spike file needs to be known by all nodes
  file_spikes_tmp = fio.file_spike_tmp(dproj)  
  net = network.NetworkOnNode(p) # Create network from net's Network class
  debug = 0 # debug: off (0), on (1)
  # create rotating data files and dirs on ONE central node
  doutf = {} # stores output file paths
  if rank == 0: doutf, v_debug = setoutfiles(ddir,expmt_group,exp_prefix,debug)
  t_vec = h.Vector(); t_vec.record(h._ref_t) # time recording
  dp_rec_L2 = h.Vector(); dp_rec_L2.record(h._ref_dp_total_L2) # L2 dipole recording
  dp_rec_L5 = h.Vector(); dp_rec_L5.record(h._ref_dp_total_L5) # L5 dipole recording  
  pc.set_maxstep(10) # sets the default max solver step in ms (purposefully large)
  h.finitialize() # initialize cells to -65 mV, after all the NetCon delays have been specified
  if rank == 0: 
    for tt in range(0,int(h.tstop),printdt): h.cvode.event(tt, prsimtime) # print time callbacks
  h.fcurrent()  
  h.frecord_init() # set state variables if they have been changed since h.finitialize
  pc.psolve(h.tstop) # actual simulation - run the solver
  pc.allreduce(dp_rec_L2, 1); pc.allreduce(dp_rec_L5, 1) # combine dp_rec on every node, 1=add contributions together  
  net.aggregate_currents() # aggregate the currents independently on each proc
  # combine net.current{} variables on each proc
  pc.allreduce(net.current['L5Pyr_soma'], 1); pc.allreduce(net.current['L2Pyr_soma'], 1)

  # write time and calculated dipole to data file only if on the first proc
  # only execute this statement on one proc
  savedat(p,dproj,f_psim,rank,doutf,t_vec,dp_rec_L2,dp_rec_L5,net,debug,exp_prefix,t_trial_start,simindex)

  # print runtimes
  if rank == 0:
    # print qnd mean
    print("Total runtime: %4.4f s, Mean runtime: %4.4f s" % (np.sum(t_sims), np.mean(t_sims)))
    # this prints a newline without having to specify it.
    print("")

  if pc.nhost() > 1:
    pc.runworker()
    pc.done()
    t1 = time.time()
    if rank == 0:
      print("Simulation run time: %4.4f s" % (t1-t0))
      print("Simulation directory is: %s" % ddir.dsim)
  else:
    # end clock time
    t1 = time.time()
    print("Simulation run time: %4.4f s" % (t1-t0))

  runanalysis(ddir,p) # run spectral analysis
  savefigs(ddir,p,p_exp) # save output figures

  if pc.nhost() > 1: h.quit()

if __name__ == "__main__":
  # reads the specified param file
  foundprm = False
  for i in range(len(sys.argv)):
    if sys.argv[i].endswith('.param'):
      f_psim = sys.argv[i]
      foundprm = True
      print('using ',f_psim,' param file.')
      break
  if not foundprm:
    f_psim = "param/default.param"
    print("Using param/default.param")
    runsim(f_psim)
