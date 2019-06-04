import numpy as np
from scipy.ndimage import filters
from scipy.ndimage import gaussian_filter
from scipy import ndimage
import math
from suite2p import utils, register, sparsedetect, classifier
import time
from scipy.sparse import csr_matrix
from scipy import stats
import os

def tic():
    return time.time()
def toc(i0):
    return time.time() - i0

def create_cell_masks(ops, stat):
    '''creates cell masks for ROIs in stat and computes radii
    inputs:
        stat, Ly, Lx, allow_overlap
            from stat: ypix, xpix, lam
            allow_overlap: boolean whether or not to include overlapping pixels in cell masks (default: False)
    outputs:
        stat, cell_pix (Ly,Lx), cell_masks (ncells, Ly, Lx)
            assigned to stat: ipix (non-overlapping if chosen), radius (minimum of 3 pixels)
    '''
    Ly=ops['Ly']
    Lx=ops['Lx']
    ncells = len(stat)
    cell_pix = np.zeros((Ly,Lx))
    #cell_masks = np.zeros((ncells,Ly,Lx), np.float32)
    for n in range(ncells):
        #if allow_overlap:
        overlap = np.zeros((stat[n]['npix'],), bool)
        ypix = stat[n]['ypix'][~overlap]
        xpix = stat[n]['xpix'][~overlap]
        lam  = stat[n]['lam'][~overlap]
        ipix = utils.sub2ind((Ly,Lx), stat[n]['ypix'], stat[n]['xpix'])
        stat[n]['ipix'] = ipix
        if xpix.size:
            # compute radius of neuron (used for neuropil scaling)
            radius = utils.fitMVGaus(ypix/ops['diameter'][0], xpix/ops['diameter'][1],lam,2)[2]
            stat[n]['radius'] = radius[0] * np.mean(ops['diameter'])
            stat[n]['aspect_ratio'] = 2 * radius[0]/(.01 + radius[0] + radius[1])
            # add pixels of cell to cell_pix (pixels to exclude in neuropil computation)
            cell_pix[ypix[lam>0],xpix[lam>0]] += 1
            #cell_masks[n, ypix, xpix] = lam / lam.sum()
        else:
            stat[n]['radius'] = 0
            stat[n]['aspect_ratio'] = 1
    cell_pix = np.minimum(1, cell_pix)
    return stat, cell_pix#, cell_masks

def circle_neuropil_masks(ops, stat, cell_pix):
    '''creates surround neuropil masks for ROIs in stat using gradually extending circles
    inputs:
        ops, stat, cell_pix
            from ops: inner_neuropil_radius, outer_neuropil_radius, min_neuropil_pixels, ratio_neuropil_to_cell
            from stat: med, radius
            cell_pix: (Ly,Lx) matrix in which non-zero elements indicate cells
    outputs:
        neuropil_masks (ncells,Ly,Lx)
    '''
    inner_radius = int(ops['inner_neuropil_radius'])
    outer_radius = ops['outer_neuropil_radius']
    # if outer_radius is infinite, define outer radius as a multiple of the cell radius
    if np.isinf(ops['outer_neuropil_radius']):
        min_pixels = ops['min_neuropil_pixels']
        ratio      = ops['ratio_neuropil_to_cell']
    # dilate the cell pixels by inner_radius to create ring around cells
    expanded_cell_pix = ndimage.grey_dilation(cell_pix, (inner_radius,inner_radius))

    ncells = len(stat)
    Ly = cell_pix.shape[0]
    Lx = cell_pix.shape[1]
    neuropil_masks = np.zeros((ncells,Ly,Lx),np.float32)
    x,y = np.meshgrid(np.arange(0,Lx),np.arange(0,Ly))
    for n in range(0,ncells):
        cell_center = stat[n]['med']
        if stat[n]['radius'] > 0:
            if np.isinf(ops['outer_neuropil_radius']):
                cell_radius  = stat[n]['radius']
                outer_radius = ratio * cell_radius
                npixels = 0
                # continue increasing outer_radius until minimum pixel value reached
                while npixels < min_pixels:
                    neuropil_on       = (((y - cell_center[1])**2 + (x - cell_center[0])**2)**0.5) <= outer_radius
                    neuropil_no_cells = neuropil_on - expanded_cell_pix > 0
                    npixels = neuropil_no_cells.astype(np.int32).sum()
                    outer_radius *= 1.25
            else:
                neuropil_on       = ((y - cell_center[0])**2 + (x - cell_center[1])**2)**0.5 <= outer_radius
                neuropil_no_cells = neuropil_on - expanded_cell_pix > 0
            npixels = neuropil_no_cells.astype(np.int32).sum()
            neuropil_masks[n,:,:] = neuropil_no_cells.astype(np.float32) / npixels
    return neuropil_masks

def create_neuropil_masks(ops, stat, cell_pix):
    '''creates surround neuropil masks for ROIs in stat by EXTENDING ROI (SLOW!!)
    inputs:
        ops, stat, cell_pix
            from ops: inner_neuropil_radius, outer_neuropil_radius, min_neuropil_pixels, ratio_neuropil_to_cell
            from stat: ypix, xpix
            cell_pix: (Ly,Lx) matrix in which non-zero elements indicate cells
    outputs:
        neuropil_masks (ncells,Ly,Lx)
    '''
    ncells = len(stat)
    Ly = cell_pix.shape[0]
    Lx = cell_pix.shape[1]
    outer_radius = ops['outer_neuropil_radius']
    neuropil_masks = np.zeros((ncells,Ly,Lx), np.float32)
    # if outer_radius is infinite, define outer radius as a multiple of the cell radius
    if np.isinf(ops['outer_neuropil_radius']):
        min_pixels = ops['min_neuropil_pixels']
        ratio      = ops['ratio_neuropil_to_cell']
    for n in range(ncells):
        ypix = stat[n]['ypix']
        xpix = stat[n]['xpix']
        # first extend to get ring of dis-allowed pixels
        ypix, xpix = sparsedetect.extendROI(ypix, xpix, Ly, Lx,ops['inner_neuropil_radius'])
        # count how many pixels are valid
        nring = np.sum(cell_pix[ypix,xpix]<.5)
        ypix1,xpix1 = ypix,xpix
        for j in range(0,100):
            ypix1, xpix1 = sparsedetect.extendROI(ypix1, xpix1, Ly, Lx, 5) # keep extending
            if np.sum(cell_pix[ypix1,xpix1]<.5)-nring>ops['min_neuropil_pixels']:
                break # break if there are at least a minimum number of valid pixels

        ix = cell_pix[ypix1,xpix1]<.5
        ypix1, xpix1 = ypix1[ix], xpix1[ix]
        neuropil_masks[n,ypix1,xpix1] = 1.
        neuropil_masks[n,ypix,xpix] = 0
    S = np.sum(neuropil_masks, axis=(1,2))
    neuropil_masks /= S[:, np.newaxis, np.newaxis]
    return neuropil_masks

def extractF(ops, stat, neuropil_masks, reg_file):
    nimgbatch = 1000
    nframes = int(ops['nframes'])
    Ly = ops['Ly']
    Lx = ops['Lx']
    ncells = neuropil_masks.shape[0]
    k0 = time.time()

    F    = np.zeros((ncells, nframes),np.float32)
    Fneu = np.zeros((ncells, nframes),np.float32)

    reg_file = open(reg_file, 'rb')
    nimgbatch = int(nimgbatch)
    block_size = Ly*Lx*nimgbatch*2
    ix = 0
    data = 1

    ops['meanImg'] = np.zeros((Ly,Lx))
    k=0
    while data is not None:
        buff = reg_file.read(block_size)
        data = np.frombuffer(buff, dtype=np.int16, offset=0)
        nimg = int(np.floor(data.size / (Ly*Lx)))
        if nimg == 0:
            break
        data = np.reshape(data, (-1, Ly, Lx)).astype(np.float32)
        inds = ix+np.arange(0,nimg,1,int)
        ops['meanImg'] += data[~ops['badframes'][inds],:,:].mean(axis=0)
        data = np.reshape(data, (nimg,-1)).transpose()
        # extract traces and neuropil
        for n in range(ncells):
            F[n,inds] = (data[stat[n]['ipix'],:] * stat[n]['lam'][:,np.newaxis]).sum(axis=0)
        Fneu[:,inds] = neuropil_masks @ data
        if ix%(5*nimg)==0:
            print('extracted %d/%d frames in %3.2f sec'%(ix+nimg,ops['nframes'], toc(k0)))
        ix += nimg
        k += 1
    print('extracted %d/%d frames in %3.2f sec'%(ix,ops['nframes'], toc(k0)))
    ops['meanImg'] /= k

    reg_file.close()
    return F, Fneu, ops

def masks_and_traces(ops, stat):
    ''' main extraction function
        inputs: ops and stat
        creates cell and neuropil masks and extracts traces
        returns: F (ROIs x time), Fneu (ROIs x time), F_chan2, Fneu_chan2, ops, stat
        F_chan2 and Fneu_chan2 will be empty if no second channel
    '''
    stat,cell_pix  = create_cell_masks(ops, stat)
    neuropil_masks = create_neuropil_masks(ops,stat,cell_pix)
    Ly=ops['Ly']
    Lx=ops['Lx']
    neuropil_masks = np.reshape(neuropil_masks, (-1,Ly*Lx))

    stat0 = []
    for n in range(len(stat)):
        stat0.append({'ipix':stat[n]['ipix'],'lam':stat[n]['lam']/stat[n]['lam'].sum()})
    F,Fneu,ops = extractF(ops, stat0, neuropil_masks, ops['reg_file'])
    if 'reg_file_chan2' in ops:
        F_chan2, Fneu_chan2, ops2 = extractF(ops.copy(), stat0, neuropil_masks, ops['reg_file_chan2'])
        ops['meanImg_chan2'] = ops2['meanImg_chan2']
    else:
        F_chan2, Fneu_chan2 = [], []

    return F, Fneu, F_chan2, Fneu_chan2, ops, stat

def roi_detect_and_extract(ops):
    i0 = tic()
    ops['diameter'] = np.array(ops['diameter'])
    if ops['diameter'].size<2:
        ops['diameter'] = int(np.array(ops['diameter']))
        ops['diameter'] = np.array((ops['diameter'], ops['diameter']))
    ops['diameter'] = np.array(ops['diameter']).astype('int32')
    print(ops['diameter'])
    ops, stat = sparsedetect.sparsery(ops)
    print('time %4.4f. Found %d ROIs'%(toc(i0), len(stat)))

    ### apply default classifier ###
    classfile = os.path.join(os.path.abspath(os.path.dirname(__file__)),
        "classifiers/classifier_user.npy",
    )
    if not os.path.isfile(classfile):
        classorig = os.path.join(os.path.abspath(os.path.dirname(__file__)),
            "classifiers/classifier.npy"
        )
        shutil.copy(classorig, classfile)
    print(classfile)
    if len(stat) > 0:
        iscell = classifier.run(classfile, stat, keys=['npix_norm', 'compact'])
        ic = (iscell[:,0]>0.5).flatten().astype(np.bool)
        stat = stat[ic]
        iscell = iscell[ic, :]
    else:
        iscell = np.zeros((0,2))
    np.save(os.path.join(ops['save_path'],'iscell.npy'), iscell)

    stat = sparsedetect.get_overlaps(stat,ops)
    #stat, ix = remove_overlaps(stat, ops, ops['Ly'], ops['Lx'])

    # extract fluorescence and neuropil
    F, Fneu, F_chan2, Fneu_chan2, ops, stat = masks_and_traces(ops, stat)
    print('time %4.4f. Extracted fluorescence from %d ROIs'%(toc(i0), len(stat)))
    # subtract neuropil
    dF = F - ops['neucoeff'] * Fneu

    # compute activity statistics for classifier
    sk = stats.skew(dF, axis=1)
    sd = np.std(dF, axis=1)
    for k in range(F.shape[0]):
        stat[k]['skew'] = sk[k]
        stat[k]['std']  = sd[k]

    # if second channel, detect bright cells in second channel
    fpath = ops['save_path']
    if 'meanImg_chan2' in ops:
        if 'chan2_thres' not in ops:
            ops['chan2_thres'] = 0.65
        ops, redcell = chan2detect.detect(ops, stat)
        np.save(os.path.join(fpath, 'redcell.npy'), redcell)
        np.save(os.path.join(fpath, 'F_chan2.npy'), F_chan2)
        np.save(os.path.join(fpath, 'Fneu_chan2.npy'), Fneu_chan2)

    # add enhanced mean image
    ops = utils.enhanced_mean_image(ops)
    # save ops
    np.save(ops['ops_path'], ops)
    # save results
    np.save(os.path.join(fpath,'F.npy'), F)
    np.save(os.path.join(fpath,'Fneu.npy'), Fneu)
    np.save(os.path.join(fpath,'stat.npy'), stat)
    iscell = np.ones((len(stat),2))
    np.save(os.path.join(fpath, 'iscell.npy'), iscell)
    print('results saved to %s'%ops['save_path'])
    return ops