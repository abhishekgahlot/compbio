#
# SINDIR library
#
#


# rasmus libs
#from rasmus import algorithms
from rasmus import fasta
from rasmus import matrix
from rasmus import phyloutil
from rasmus import stats
from rasmus import treelib
from rasmus import util
from rasmus import phylip

# python libs
import math, StringIO, copy, random, sys


# scipy libs
# (needed for numerical integration and least square error fitting)
import scipy
import scipy.linalg
import scipy.integrate
import scipy.optimize



#-------------------------------------------------------------------------------
# debugging variables and functions
#-------------------------------------------------------------------------------
DEBUG = sys.stdout

DEBUG_LEVEL = 0

DEBUG_NONE = 0
DEBUG_LOW = 1
DEBUG_MED = 2
DEBUG_HIGH = 3


def setDebug(level=DEBUG_NONE):
    global DEBUG_LEVEL
    DEBUG_LEVEL = level

def isDebug(level):
    return DEBUG_LEVEL >= level


def debug(* text, **args):
    args.setdefault("level", DEBUG_NONE)

    # check debug level
    if DEBUG_LEVEL < args["level"]:
        return

    output = " ".join(map(str, text))
    
    if "nonl" in args:
        DEBUG.write(output)
    else:
        print >>DEBUG, output



def setDebugStream(stream):
    globals()["DEBUG"] = stream


def drawTreeLogl(tree, out=None, events={}, baserate=1.0):
    labels = {}
    
    if out == None:
        out = DEBUG
    
    if "baserate" in tree.data:
        baserate = tree.data["baserate"]
    
    
    for node in tree.nodes.values():
        notes = ""
        if "extra" in node.data:
            notes += "E"
        if "unfold" in node.data:
            notes += "U"
        
        if "logl" in node.data:
            if isinstance(node.data["logl"], float):
                labels[node.name] = "[%s]\n%.3f (%.3f) %s" % \
                    (node.name, node.dist, node.data["logl"], notes)
                #logl += node.data["logl"]
            else:
                labels[node.name] = "[%s]\n%.3f (%s) %s" % \
                    (node.name, node.dist, str(node.data["logl"]), notes)

        else:
            labels[node.name] = "[%s]\n%.3f (*) %s" % \
                (node.name, node.dist, notes)
        
        if "params" in node.data:
            fracs = map(stats.mean, zip(* node.data["fracs"]))
            mean = sum(util.vmul(util.cget(node.data["params"], 0), fracs))
            sdev = sum(util.vmul(util.cget(node.data["params"], 1), fracs))
            
            mean *= baserate
            sdev *= baserate
            
            labels[node.name] += "\n%.3f %.3f" % (mean, sdev)
        
        if node in events:
            labels[node.name] += " %s" % events[node]
        
    if "logl" in tree.data:
        debug("logl:      %f" % tree.data["logl"])
        debug("eventlogl: %f" % tree.data["eventlogl"])
        debug("errorlogl: %f" % tree.data["errorlogl"])
    debug("baserate:  %f" % baserate)
    debug("treelen:   %f" % sum(x.dist for x in tree.nodes.values()))
    if "error" in tree.data:
        debug("error:     %f" % tree.data["error"])
    
    treelib.drawTree(tree, minlen=20, labels=labels, spacing=4, 
                        labelOffset=-3, out=out)



class SindirError (Exception):
    def __init__(self, msg):
        Exception.__init__(self)
        self.msg = msg
    def __str__(self):
        return str(self.msg)



def printVisitedTrees(visited):
    if len(visited) == 0:
        return
    nleaves = len(visited.values()[0][1].leaves())
    
    debug("\n\nmost likily trees out of %d visited (%5.1f total): " % \
          (len(visited), float(numPossibleTrees(nleaves))))
    
    mat = [[key, logl, 
           tree.data["error"], 
           tree.data["baserate"],
           count]
           for key, (logl, tree, count) in visited.iteritems()]
    mat.sort(key=lambda x: x[1], reverse=True)
    
    util.printcols([["TREE", "LOGL", "ERROR", "BASERATE", "COUNT"]] + 
                   mat[:80], spacing=4, out=DEBUG)
    debug()

    mat.sort(key=lambda x: x[2])
    util.printcols([["TREE", "LOGL", "ERROR", "BASERATE", "COUNT"]] + 
                   mat[:80], spacing=4, out=DEBUG)
    debug()


#-------------------------------------------------------------------------------
# SINDIR input/output
#-------------------------------------------------------------------------------

def writeParams(filename, params):
    """Write SINDIR model parameters to a file"""
    
    out = file(filename, "w")
    
    keys = util.sort(params.keys())
    
    for key in keys:
        values = params[key]
        print >>out, "%s\t%s" % (str(key), "\t".join(map(str,values)))


def readParams(filename):
    """Read SINDIR model parameters to a file"""
    
    infile = file(filename)
    params = {}
    
    for line in infile:
        tokens = line.split("\t")
        key = tokens[0]
        values = tokens[1:]
        if key[0].isdigit():
            key = int(key)
        params[key] = map(float, values)
        
    return params


def readLabels(filename):
    """Read gene names from a file"""
    
    if filename.endswith(".fasta") or \
       filename.endswith(".fa") or \
       filename.endswith(".align"):
        labels = fasta.readFasta(filename).keys()
    else:
        labels = util.readStrings(filename)
    
    return labels


def writeTreeDistrib(out, lengths):
    out = util.openStream(out, "w")

    for node, lens in lengths.items():
        if len(lens) == 0 or max(lens) == min(lens):
            continue

        out.write(str(node.name))

        for length in lens:
            out.write("\t%f" % length)
        out.write("\n")


def readTreeDistrib(filename):
    infile = util.openStream(filename)
    lengths = {}
    
    for line in infile:
        tokens = line.split("\t")
        name = tokens[0]
        
        if name.isdigit():
            name = int(name)
        
        lengths[name] = map(float, tokens[1:])
    
    return lengths


def outTreeFile(conf):
    return conf["out"] + ".tree"


def debugFile(conf):
    return conf["out"] + ".debug"



#-------------------------------------------------------------------------------
# Branch length fitting
#-------------------------------------------------------------------------------

def neighborjoin(distmat, genes):
    tree = treelib.Tree()
    leaves = {}
    dists = util.Dict(2, None)
    restdists = {}
    
    
    # initialize distances
    for i in range(len(genes)):
        r = 0
        for j in range(len(genes)):
            dists[genes[i]][genes[j]] = distmat[i][j]
            r += distmat[i][j]
        restdists[genes[i]] = r / (len(genes) - 2)
        
    # initialize leaves
    for gene in genes:
        tree.add(treelib.TreeNode(gene))
        leaves[gene] = 1
    
    # join loop
    while len(leaves) > 2:       
        # search for closest genes
        low = util.INF
        lowpair = (None, None)
        leaveslst = leaves.keys()

        for i in range(len(leaves)):
            for j in range(i+1, len(leaves)):
                gene1, gene2 = leaveslst[i], leaveslst[j]
                dist = dists[gene1][gene2] - restdists[gene1] - restdists[gene2]
                
                if dist < low:
                    low = dist
                    lowpair = (gene1, gene2)
        
        # join gene1 and gene2
        gene1, gene2 = lowpair
        parent = treelib.TreeNode(tree.newName())
        tree.addChild(parent, tree.nodes[gene1])
        tree.addChild(parent, tree.nodes[gene2])
        
        # set distances
        tree.nodes[gene1].dist = (dists[gene1][gene2] + restdists[gene1] - 
                                  restdists[gene2]) / 2.0
        tree.nodes[gene2].dist = dists[gene1][gene2] - tree.nodes[gene1].dist
        
        # gene1 and gene2 are no longer leaves
        del leaves[gene1]
        del leaves[gene2]
        
        gene3 = parent.name
        r = 0
        for gene in leaves:
            dists[gene3][gene] = (dists[gene1][gene] + dists[gene2][gene] -
                                  dists[gene1][gene2]) / 2.0
            dists[gene][gene3] = dists[gene3][gene]
            r += distmat[i][j]
        leaves[gene3] = 1
        
        if len(leaves) > 2:
            restdists[gene3] = r / (len(leaves) - 2)
    
    # join the last two genes into a tribranch
    gene1, gene2 = leaves.keys()
    if type(gene1) == str:
        gene1, gene2 = gene2, gene1
    tree.addChild(tree.nodes[gene1], tree.nodes[gene2])
    tree.nodes[gene2].dist = dists[gene1][gene2]
    tree.root = tree.nodes[gene1]
    
    return tree



def findSplits(network, leaves):
    # find vertice and edge visit history
    start = network.keys()[0]

    openset = [start]
    closedset = {}
    
    vhistory = []
    ehistory = []
    elookup = util.Dict(1, [])
    
    
    while len(openset) > 0:
        vertex = openset.pop()
        
        vhistory.append(vertex)
        
        if len(vhistory) > 1:
            edge = tuple(util.sort(vhistory[-2:]))        
            ehistory.append(edge)
            elookup[edge].append(len(ehistory) - 1)
        
        # skip closed vertices
        if vertex in closedset:
            continue
        
        for v in network[vertex].keys():
            if v not in closedset:
                openset.append(vertex)            
                openset.append(v)
        

        # close new vertex
        closedset[vertex] = 1
    
    
    # use histories to define half each split
    splits = {}
    for edge in elookup:
        set1 = {}
        
        start, end = elookup[edge]
        for i in range(start+1, end+1):
            if vhistory[i] in leaves:
                set1[vhistory[i]] = 1
        
        # fill in other half of splits using complement
        set2 = {}
        for v in leaves:
            if v not in set1:
                set2[v] = 1
        
        if edge[0] == vhistory[start]:
            splits[edge] = [set2, set1]
        else:
            splits[edge] = [set1, set2]
        
    
    return splits

def makeVector(array):
    if len(array.shape) == 2:
        if array.shape[0] == 1:
            return array[0]
        else:
            return scipy.transpose(array)[0]
    else:
        return array

def setTreeDistances(conf, tree, distmat, genes):
    if isDebug(DEBUG_MED):
        util.tic("fit branch lengths")
    
    
    if not treelib.isRooted(tree):
        tree.addChild(treelib.TreeNode(tree.newName()), tree.root)
        tree.root = tree.root.parent
    network = treelib.tree2graph(tree)
        
    # create pairwise dist array
    dists = []
    for i in xrange(len(genes)):
        for j in xrange(i+1, len(genes)):
            dists.append(distmat[i][j])
    
    # find how edges split vertices
    splits = findSplits(network, util.makeset(genes))
    edges = splits.keys()
    
    # create topology matrix
    topmat = matrix.makeMatrix(len(dists), len(edges))
    
    vlookup = util.list2lookup(genes)
    n = len(genes)
    for e in xrange(len(edges)):
        set1, set2 = splits[edges[e]]
        for gene1 in set1:
            for gene2 in set2:
                i, j = util.sort([vlookup[gene1], vlookup[gene2]])
                index = i*n-i*(i+1)/2+j-i-1
                topmat[index][e] = 1
    
        
    A = scipy.array(topmat)
    d = scipy.array(dists)
    b,resids,rank,singlars = scipy.linalg.lstsq(A, d)
    
    # force non-zero branch lengths
    b = [max(float(x), 0) for x in makeVector(b)]
    #b = [float(x) for x in makeVector(b)]
    
    
    for i in xrange(len(edges)):
        gene1, gene2 = edges[i]
        if tree.nodes[gene2].parent == tree.nodes[gene1]:
            gene1, gene2 = gene2, gene1
        tree.nodes[gene1].dist = b[i]
    
    for node in tree.nodes.values():
        assert node.dist >= 0
    
    resids = makeVector(scipy.matrixmultiply(A, b)) - d
    tree.data["error"] = math.sqrt(scipy.dot(resids, resids)) / \
                                   sum(x.dist for x in tree.nodes.values())
    
    if len(tree.root.children) == 1:
        tree.root = tree.root.children[0]
        tree.remove(tree.root.parent)
        tree.root.parent = None
    
    
    if isDebug(DEBUG_MED):
        util.toc()


#-------------------------------------------------------------------------------
# Learning
#-------------------------------------------------------------------------------

def variance2(vals, u):
    return sum(map(lambda x: (x - u)**2, vals)) / float(len(vals)-1)

def sdev2(vals, u):
    return math.sqrt(variance2(vals, u))

def fitNormal(lens):
    ndivs = int((max(lens) - min(lens)) / stats.mean(lens) *  40)
    x, y = util.hist(lens, ndivs)
    mu = x[util.argmax(y)]
    data = filter(util.withinfunc(0, mu*2), lens)

    if len(data) < 2:
        sigma = 1
    else:
        sigma = sdev2(data, mu)
    
    return mu, sigma

def fitNormal2(lens):
    mu = stats.mean(lens)
    sigma = stats.sdev(lens)
    param, resid = stats.fitDistrib(stats.normalPdf, 
                                    [mu, sigma],
                                    lens,
                                    mu - 2*sigma,
                                    mu + 2*sigma,
                                    sigma / min(30, len(lens)/5))
    return param


def fitParams(lengths, baserates, gene2species, fit=True):
    ntrees = len(lengths.values()[0])
    
    params = {}
    
    dist = util.distrib(baserates, width=.01)
    top = min(max(baserates, 10))
    param, resid = stats.fitCurve(dist[0], dist[1], stats.gammaPdf, [0,top])
    params["baserate"] = param
    
    
    util.tic("fitting params")
    for node, lens in lengths.items():
        if len(lens) == 0 or max(lens) == min(lens):
            params[node.name] = [0, 1]
            continue
        
        #util.tic("fitting params for " + str(node.name))
        
        lens = util.vdiv(lens, baserates)
        
        if fit:
            mu = stats.mean(lens)
            sigma = stats.sdev(lens)            
            param, resid = stats.fitDistrib(stats.normalPdf, 
                                            [mu, sigma],
                                            lens,
                                            mu - 3*sigma,
                                            mu + 3*sigma,
                                            sigma / 10)
            params[node.name] = param 
        else:
            ndivs = int((max(lens) - min(lens)) / stats.mean(lens) *  40)
            x, y = util.hist(lens, ndivs)
            mu = x[util.argmax(y)]
            data = filter(util.withinfunc(0, mu*2), lens)
            
            if len(data) < 2:
                sigma = 1
            else:
                sigma = sdev2(data, mu)
            
            #mu = stats.mean(lens)
            #sigma = stats.sdev(lens)
            
            params[node.name] = [mu, sigma]
        
        #util.toc()
    util.toc()
    
    return params


def dataLikelihood(lenmat, baserates, means, sdevs, baserateparam):
    logl = 0
    
    for i in range(len(lenmat)):
        for j in range(len(lenmat[i])):
            logl += log(stats.normalPdf(lenmat[i][j]/baserates[i], 
                                        [means[j], sdevs[j]]))
    
        # calc baserate logl
        logl += stats.gammaPdf(baserates[i], baserateparam)
    
    return logl


def mleBaserates(lengths, params, baserateparam):
    lenmat = zip(* lengths.values())
    keys = map(lambda x: x.name, lengths.keys())
    means, sdevs = zip(* util.sublist(params, keys))
    baserates = []
    for i in xrange(len(lenmat)):
        baserates.append(mleBaserate(lenmat[i], means, sdevs, baserateparam))
    return baserates


def learnModel(trees, stree, gene2species, statsprefix=""):
    util.tic("learn model")

    util.tic("find branch length distributions")
    trees2, lengths = phyloutil.findBranchDistrib(trees, stree, gene2species,
                                                  False)
    debug("Total trees matching species topology: %d out of %d" % 
          (len(trees2), len(trees)))
    util.toc()
    
    params = {}
    
    totlens = map(sum, zip(* lengths.values()))
    
    # print output stats
    if statsprefix != "":
        writeTreeDistrib(file(statsprefix + ".lens", "w"), lengths)
    
    
    util.tic("fitting params")
    for node, lens in lengths.items():
        if len(lens) == 0 or max(lens) == min(lens):
            continue
        
        util.tic("fitting params for " + str(node.name))
        
        #ndivs = int(max(lens) / .001)
        #dist = util.distrib(lens, size=.001)
        #param, resid = stats.fitCurve(dist[0], dist[1], stats.normalPdf, [1,1])
        #param, resid = stats.fitCurve(dist[0], dist[1], stats.gammaDistrib, [1,1])
        
        param = fitNormal2(util.vdiv(lens, totlens))
        
        params[node.name] = param
        util.toc()
    util.toc()

    # calc distribution of total tree length
    lens = map(lambda x: sum(y.dist for y in x.nodes.values()), trees2)
    lens = filter(lambda x: x < 20, lens)
    mu = stats.mean(lens)
    lens = filter(lambda x: x < 2*mu, lens)
    mu = stats.mean(lens)
    sigma2 = stats.variance(lens)
    params["baserate"] = [mu*mu/sigma2, mu/sigma2]
    params[stree.root.name] = [0, 1]
    #util.writeVector("treelens", lens)
    
    util.toc()
    
    return params
    

def learnModel2(lengths, gene2species, niters=10, fit=True):
    lenmat = zip(* lengths.values())
    keys = map(lambda x: x.name, lengths.keys())

    # init base rates
    baserates = map(sum, lenmat)
    
    baseratesList = [baserates]
    paramsList = []
    
    # fit baserate distribution
    dist = util.distrib(baserates, width=.2)
    baserateparam, resid = stats.fitCurve(dist[0], dist[1], stats.gammaPdf, [1,1])
    
    
    # do EM
    for i in range(niters):
        params = fitParams(lengths, baserates, gene2species, fit=fit)
        means, sdevs = zip(* util.sublist(params, keys))
        
        paramsList.append(params)
        
        baserates = []
        for i in xrange(len(lenmat)):
            baserates.append(mleBaserate(lenmat[i], means, sdevs, baserateparam))
        
        
        #factor = stats.mean(util.vdiv(baseratesList[0], baserates))
        #baserates = [x*factor for x in baserates]
        
        baseratesList.append(baserates)
        
        # calc likelihood
        util.log(dataLikelihood(lenmat, baserates, means, sdevs, baserateparam))
        
    
    return paramsList, baseratesList



#-------------------------------------------------------------------------------
# Likelihood calculation
#-------------------------------------------------------------------------------


def mleBaserate2(lens, means, sdevs, baserateparam):
    vars = util.vmul(sdevs, sdevs)
    return sum(util.vdiv(util.vmul(lens, lens), vars)) / \
           sum(util.vdiv(util.vmul(means, lens), vars))


def mleBaserate(lens, means, sdevs, baserateparam):
    [alpha, beta] = baserateparam
    
    # protect against zero
    ind = util.findgt(.0001, sdevs)
    lens = util.sublist(lens, ind)
    means = util.sublist(means, ind)
    sdevs = util.sublist(sdevs, ind)
    
    a = (1 - alpha) / beta
    b = sum(means[i] * lens[i] / sdevs[i]**2
            for i in range(len(lens))) / beta
    c = - sum(lens[i] ** 2 / sdevs[i] ** 2
              for i in range(len(lens))) / beta
    
    #print filter(lambda x: x>0, stats.solveCubic(a, b, c))
    return max(stats.solveCubic(a, b, c))


def log(x):
    """Safe logarithm function"""
    
    if x <= 0:
        return -util.INF
    else:
        return math.log(x)


def getExtraBranches(root, recon, events, stree):
    extraBranches = {}

    # determine if any extra branches exist
    def markExtras(node):
        if recon[node] == stree.root and \
           events[node] == "dup":
            for child in node.children:
                if recon[child] != stree.root:
                    extraBranches[child] = 1
                    child.data["extra"] = 1
        node.recurse(markExtras)
    markExtras(root)
     
    return extraBranches


def getBaserate(tree, stree, params, recon=None, gene2species=None):
    if recon == None:
        assert gene2species != None
        recon = phyloutil.reconcile(tree, stree, gene2species)
    events = phyloutil.labelEvents(tree, recon)
    
    extraBranches = getExtraBranches(tree.root, recon, events, stree)
    
    lens = []
    means = []
    sdevs = []
    
    # process each child of subtree root
    def walk(node, depths, sroot, extra):
        # save depth of node
        if recon[node] != recon[tree.root]:  #stree.root:
            depths[node] = node.dist + depths[node.parent]
        else:
            # ignore branch length of free branches
            depths[node] = depths[node.parent]
        
        
        # record presence of extra in path
        extra = extra or ("extra" in node.data)
        
        
        if events[node] == "dup":
            # recurse within dup-only subtree
            #   therefore pass depths and sroot unaltered
            node.recurse(walk, depths, sroot, extra)
        else:
            # we are at subtree leaf
            
            # figure out species branches that we cross
            # get total mean and variance of this path            
            mu = 0
            sigma2 = 0            
            snode = recon[node]
            
            # branch is also free if we do not cross any more species
            # don't estimate baserates from extra branches
            if snode != sroot and not extra:
                
                while snode != sroot and snode != stree.root:
                    mu += params[snode.name][0]
                    sigma2 += params[snode.name][1]**2
                    snode = snode.parent
                assert abs(sigma2) > .00000001, "sigma too small"
                sigma = math.sqrt(sigma2)
                
                # save dist and params
                lens.append(depths[node])
                means.append(mu)
                sdevs.append(sigma)
            
            # continue recursion, but with new depths and sroot
            for child in node.children:
                walk(child, depths={node: 0}, sroot=recon[node], extra=False)
    
    
    for child in tree.root.children:
        walk(child, depths={tree.root: 0}, sroot=recon[tree.root], extra=False)
    
    
    #baserate = mleBaserate(lens, means, sdevs, params["baserate"])
    
    #util.printcols(zip(lens, means, sdevs, util.vdiv(lens, means)))
    
    baserate = mleBaserate2(lens, means, sdevs, params["baserate"])
        
    return baserate



def subtreeLikelihood(conf, tree, root, recon, events, stree, params, baserate):
    this = util.Closure(
        logl=0.0,
        isExtra = False
    )
    
    extraBranches = getExtraBranches(root, recon, events, stree)
    depths = {root.parent: 0}
    marks = {root.parent: 1}
    sroot = recon[root.parent]
    
    
    # process each child of subtree root
    def walk(node, extra):        
        # save depth of node
        if recon[node] != recon[tree.root]:  #stree.root:
            depths[node] = node.dist + depths[node.parent]
        else:
            # ignore branch length of free branches
            depths[node] = depths[node.parent]
        
        # remember if extra node is in path
        if "extra" in node.data:
            extra = node
        
        
        if events[node] == "dup":
            # recurse within dup-only subtree
            node.recurse(walk, extra)
        else:
            # we are at subtree leaf
            # compute likelihood of path from leaf to root
            
            # figure out species branches that we cross
            # get total mean and variance of this path
            mu = 0
            sigma2 = 0
            snode = recon[node]
            
            # branch is free if we do not cross any more species
            if snode == sroot:
                return
            
            # sum means and variances along path
            while snode != sroot and snode != stree.root:
                mu += params[snode.name][0]
                sigma2 += params[snode.name][1]**2
                snode = snode.parent
            assert abs(sigma2) > .00000001, "sigma too small"
            sigma = math.sqrt(sigma2)
            
            
            # find out how much of our path is conditioned
            ptr = node
            while ptr not in marks:
                marks[ptr] = 1
                ptr = ptr.parent
            assert node != ptr
            condDist = depths[ptr]
            
            if condDist == 0.0:
                # if no distance to condition on denominator is 1.0
                logdenom = log(1.0)
            else:
                logdenom = log(1 - stats.normalCdf(condDist/baserate, [mu, sigma]))
            
            # determine dist of total path
            dist = max(depths[node], condDist)
            #dist = depths[node]
            
            
            # handle extra branches
            if extra != None:
                # determine desired shrink
                target = min(mu, max(dist/baserate,0)) * baserate
                shrink = dist - target
                
                # determine how much shrink is allowed
                shrink = min(shrink, max(extra.dist, 0))
                
                if condDist == 0.0:
                    dist -= shrink
                else:
                    condDist -= shrink
            
            
            lognom = log(stats.normalPdf(dist/baserate, [mu, sigma]))
            
            if logdenom == -util.INF or \
               lognom   == util.INF:
                logl = -util.INF               
                this.logl = -util.INF
            else:
                logl = lognom - logdenom
                this.logl += logl
            
            """
            print "\t".join(["%10s" % str(node.name), 
                             "%.3f" % dist, 
                             "%.3f |" % condDist,
                             "%.3f" % (dist / baserate), 
                             "%.3f |" % (condDist / baserate), 
                             "%.3f" % mu, 
                             "%.3f |" % sigma,
                             "%.3f" % logdenom])
            """
            
            
            # debug saving
            node.data["logl"] = logl
            
            if this.logl > 1e10:
                debug(dist, condDist, baserate, mu, sigma, logl, logdenom)
                raise Exception("logl too high")
            
            node.data["params"] = [[mu, sigma]]
            node.data["fracs"] = [[1]]
            
    walk(root, None)    
    
    
    return this.logl


def branchLikelihoods(conf, tree, recon, events, stree, params, baserate):
    this = util.Closure(logl=0.0)

    # determine if top branch unfolds
    if recon[tree.root] ==  stree.root and \
       events[tree.root] == "dup":
        for child in tree.root.children:
            if recon[child] != stree.root:
                child.data["unfold"] = True    
    
    # recurse through indep sub-trees
    def walk(node):
        if events[node] == "spec" or \
           node == tree.root:
            for child in node.children:
                this.logl += subtreeLikelihood(conf, tree, child, recon, events, 
                                                stree, params, baserate)
        node.recurse(walk)
    walk(tree.root)
    
    return this.logl



def rareEventsLikelihood(conf, tree, stree, recon, events):
    logl = 0.0
    
    for node, event in events.items():
        if event == "dup":
            logl += log(conf["dupprob"])
        
    nloss = len(phyloutil.findLoss(tree, stree, recon))
    logl += nloss * log(conf["lossprob"])
    
    return logl


def treeLogLikelihood(conf, tree, stree, gene2species, params, baserate=None):
    # reconcile the gene tree
    # determine all events
    tree.clearData("logl", "extra", "fracs", "params", "unfold")
    recon = phyloutil.reconcile(tree, stree, gene2species)
    events = phyloutil.labelEvents(tree, recon)
    
    # determine baserate
    if baserate == None:
        baserate = getBaserate(tree, stree, params, recon=recon)
    
    
    # debug info
    if isDebug(DEBUG_MED):
        util.tic("find logl")

    
    # top branch is "free"
    #params[stree.root.name] = [0,0]
    this = util.Closure(logl=0.0)
    this.logl = branchLikelihoods(conf, tree, recon, events, 
                                  stree, params, baserate)
        
    
    # calc probability of rare events
    tree.data["eventlogl"] = rareEventsLikelihood(conf, tree, stree, recon, events)
    this.logl += tree.data["eventlogl"]
    
    # calc penality of error
    tree.data["errorlogl"] = tree.data["error"] * conf["errorcost"]
    this.logl += tree.data["errorlogl"]
    
    
    # debugging information
    tree.data["baserate"] = baserate
    tree.data["logl"] = this.logl
    
    if isDebug(DEBUG_MED):
        util.toc()
        debug("\n\n")
        drawTreeLogl(tree, events=events)
    
    
    return this.logl








#-------------------------------------------------------------------------------
# Tree search
#-------------------------------------------------------------------------------


def numPossibleTrees(nleaves):
    n = 1
    
    for i in range(3, 2*nleaves-5+1, 2):
        n *= i
    
    return (2*nleaves - 3) * n


def proposeNni(tree, node1, node2, change=0):
    """Proposes a new tree using Nearest Neighbor Interchange
       
       Branch for NNI is specified by giving its two incident nodes (node1 and 
       node2).  Change specifies which  subtree of node1 will be swapped with
       the uncle.  See figure below.

         node2
        /     \
      uncle    node1
               /  \
         child[0]  child[1]
    
    """
    
    # ensure node2 is parent of node1
    if node1.parent != node2:
        node1, node2 = node2, node1
    assert node1.parent == node2
    
    # find uncle
    uncle = 0 
    if node2.children[uncle] == node1:
        uncle = 1
    
    # swap parent pointers
    node1.children[change].parent = node2
    node2.children[uncle].parent = node1
    
    # swap child pointers
    node2.children[uncle], node1.children[change] = \
        node1.children[change], node2.children[uncle]


def proposeTree(conf, tree):
    tree2 = tree.copy()
    
    if random.random() < conf["rerootprob"]:
        nodes = tree.nodes.values()
        newnode = nodes[random.randint(0, len(nodes)-1)]
        tree2 = treelib.reroot(tree2, newnode.name)
    
    # find edges for NNI
    nodes = tree2.nodes.values()
    nodes = filter(lambda x: not x.isLeaf() and 
                             x != tree2.root, nodes)
    edges = [(node, node.parent) for node in nodes]
    edge = edges[int(random.random() * len(edges))]
    
    proposeNni(tree2, edge[0], edge[1], int(round(random.random())))
    return tree2


def proposeTreeWeighted(tree):
    """Nodes in tree must have logl in their data dict"""
    
    tree2 = tree.copy()
    
    # find edges for NNI
    nodes = tree2.nodes.values()
    nodes = filter(lambda x: not x.isLeaf() and 
                             x != tree2.root, nodes)
    edges = [(node, node.parent) for node in nodes]
    
    # create weights
    weights = []    
    for edge in edges:
        if "logl" in edge[0].data:
            weights.append(edge[0].data["logl"])
        else:
            weights.append(0)
    top = max(weights) + 1
    weights = [top - x for x in weights]
    #print weights
    
    # sample by weight
    edge = edges[stats.sample(weights)]
    
    proposeNni(tree2, edge[0], edge[1], int(round(random.random())))
    return tree2



def printMCMC(conf, i, tree, stree, gene2species, visited):
    if isDebug(DEBUG_LOW):
        recon = phyloutil.reconcile(tree, stree, gene2species)
        events = phyloutil.labelEvents(tree, recon)            

        debug("\n=======================================")
        debug("iter:", i, " visited:", len(visited))
        drawTreeLogl(tree, events=events)
        debug()
        debug()


class McmcChain:
    def __init__(self, name, state, logl, propose):
        self.name = name
        self.state = state
        self.logl = logl
        self.propose = propose
    
    
    def step(self):
        nextState, nextLogl = self.propose(self, self.state)

        # accept/reject
        if nextLogl > self.logl or \
           nextLogl - self.logl > log(random.random()):
            # accept new state
            self.state = nextState
            self.logl = nextLogl



def addVisited(visited, tree):
    thash = phyloutil.hashTree(tree)
    if thash in visited:
        a, b, count = visited[thash]
    else:
        count = 0
    visited[thash] = [tree.data["logl"], tree.copy(), count+1]



def searchMCMC(conf, distmat, labels, stree, gene2species, params,
               initTree=None, visited=None):
    if visited == None:
        visited = {}
    
    this = util.Closure(
        nold=0,
        toplogl = -util.INF,
        toptree = None,
        iter=0)
    
    
    # init with NJ    
    if initTree != None:
        tree = initTree
    else:
        tree = neighborjoin(distmat, labels)
        tree = phyloutil.reconRoot(tree, stree, gene2species)
        setTreeDistances(conf, tree, distmat, labels)

    # init likelihood score
    logl = treeLogLikelihood(conf, tree, stree, gene2species, params)

    # store tree in visited
    addVisited(visited, tree)
    
    # show initial tree
    printMCMC(conf, 0, tree, stree, gene2species, visited)
    
    
    # proposal function
    def propose(chain, tree):
        tree2 = proposeTree(conf, tree)
        #tree2 = proposeTree(conf, tree2)
        
        #tree2 = replaceGeneInTree(conf, tree, None, distmat, labels, stree, 
        #                          gene2species, params, visited)
        
        # check visited dict
        thash = phyloutil.hashTree(tree2)
        if thash in visited:
            logl, tree2, count = visited[thash]
            visited[thash][2] += 1
            this.nold += 1
        else:
            setTreeDistances(conf, tree2, distmat, labels)
            logl = treeLogLikelihood(conf, tree2, stree, gene2species, params)
            this.nold = 0
            visited[thash] = [logl, tree2.copy(), 1]
        
        
        # best yet tree
        if logl > this.toplogl:
            printMCMC(conf, "%d:%d" % (chain.name, this.iter), 
                      tree2, stree, gene2species, visited)
            this.toplogl = logl
            this.toptree = tree2.copy()
        
        # alter logl to influence search only
        logl += conf["speedup"] * this.nold
        
        return tree2, logl
        
    # init chains    
    chains = []
    for i in range(conf["nchains"]):
        chains.append(McmcChain(i, tree.copy(), logl, propose))
    
    
    # run chains
    for i in xrange(1, conf["maxiters"]):
        if len(visited) >= conf["iters"]:
            break
        
        this.iter += 1
        
        for chain in chains:
            chain.step()   
   

    return this.toptree, this.toplogl



def searchMCMC2(conf, distmat, labels, stree, gene2species, params,
               initTree=None, visited=None):
    if visited == None:
        visited = {}
    
    
    # init with NJ    
    if initTree != None:
        tree = initTree
    else:
        tree = neighborjoin(distmat, labels)
        tree = phyloutil.reconRoot(tree, stree, gene2species)
        setTreeDistances(conf, tree, distmat, labels)
    
    # init likelihood score
    top = treeLogLikelihood(conf, tree, stree, gene2species, params)
    toptree = tree.copy()
    
    # store tree in visited
    addVisited(visited, tree)
    
    # show initial tree
    printMCMC(conf, 0, tree, stree, gene2species, visited)
    
    
    # tree search
    nold = 0
    lastl = top
    for i in xrange(1, conf["maxiters"]):
        if len(visited) >= conf["iters"]:
            break
        
        tree2 = proposeTree(conf, tree)
        
        thash = phyloutil.hashTree(tree2)
        if thash in visited:
            logl, tree2, count = visited[thash]
            visited[thash][2] += 1
            nold += 1
        else:
            setTreeDistances(conf, tree2, distmat, labels)
            logl = treeLogLikelihood(conf, tree2, stree, gene2species, params)
            nold = 0
            visited[thash] = [logl, tree2.copy(), 1]
        
        
        # best yet tree
        if logl > top:
            printMCMC(conf, i, tree2, stree, gene2species, visited)
            top = logl
            toptree = tree2.copy()


        # accept/reject
        if logl > lastl:
            # accept new tree
            tree = tree2
            lastl = logl
        else:
            # accept with a chance
            if logl - lastl > log(random.random()) - (nold * conf["speedup"]):
                tree = tree2
                lastl = logl
        
        if nold > 0 and nold % 50 == 0:
            debug("seen %d old trees in a row, visited: %d, iter: %d" % \
                  (nold, len(visited), i))


    return toptree, top


def replaceGeneInTree(conf, tree, badgene, distmat, labels, stree, gene2species,
                    params, visited):
    
    if badgene == None:
        nodes = tree.leaves()
        weights = [x.data["logl"] for x in nodes]
        
        top = max(weights) + 1
        weights = [top - x for x in weights]
        
        badgene = nodes[stats.sample(weights)].name
    
    tree2 = tree.copy()
    tree2.remove(tree.nodes[badgene])
    treelib.removeSingleChildren(tree2)
    
    return placeGeneInTree(conf, tree2, badgene, distmat, labels, stree, gene2species,
                           params, visited)
    

def placeGeneInTree(conf, tree, newgene, distmat, labels, stree, gene2species,
                    params, visited):
    toplogl = -util.INF
    toptree = None
    
    # loop over places to add newgene
    for name in tree.nodes:
        tree2 = tree.copy()
        node = tree2.nodes[name]

        if node == tree2.root:
            newnode = treelib.TreeNode(tree2.newName())
            tree2.addChild(newnode, tree2.root)
            tree2.root = newnode
            tree2.addChild(newnode, treelib.TreeNode(newgene))
        else:
            parent = node.parent
            tree2.remove(node)
            newnode = treelib.TreeNode(tree2.newName())
            tree2.addChild(parent, newnode)
            tree2.addChild(newnode, node)
            tree2.addChild(newnode, treelib.TreeNode(newgene))
        
        setTreeDistances(conf, tree2, distmat, labels)
        logl = treeLogLikelihood(conf, tree2, stree, gene2species, params)
        
        addVisited(visited, tree2)
        
        if logl >= toplogl:
            toplogl = logl
            toptree = tree2
    
    return toptree



def searchGreedy(conf, distmat, labels, stree, gene2species, params, visited=None):
    if visited == None:
        visited = {}

    totalgenes = len(labels)
    ngenes = 2
    
    # create initial 2 gene tree (labels[0], labels[1])
    tree = treelib.Tree()
    tree.makeRoot()
    tree.addChild(tree.root, treelib.TreeNode(labels[0]))
    tree.addChild(tree.root, treelib.TreeNode(labels[1]))
    
    
    for ngenes in xrange(2, totalgenes):
        debug("adding", labels[ngenes])
        
        toplogl = -util.INF
        toptree = None
        
        distmat2 = matrix.submatrix(distmat, range(ngenes+1), range(ngenes+1))
        labels2  = labels[:ngenes+1]
        
        
        # place new gene on every branch
        for name in tree.nodes:
            tree2 = tree.copy()
            node = tree2.nodes[name]

            if node == tree2.root:
                newnode = treelib.TreeNode(tree2.newName())
                tree2.addChild(newnode, tree2.root)
                tree2.root = newnode
                tree2.addChild(newnode, treelib.TreeNode(labels[ngenes]))
            else:
                parent = node.parent
                tree2.remove(node)
                newnode = treelib.TreeNode(tree2.newName())
                tree2.addChild(parent, newnode)
                tree2.addChild(newnode, node)
                tree2.addChild(newnode, treelib.TreeNode(labels[ngenes]))
            
            #tree2 = phyloutil.reconRoot(tree2, stree, gene2species)
            setTreeDistances(conf, tree2, distmat2, labels2)
            logl = treeLogLikelihood(conf, tree2, stree, gene2species, params)

            if logl >= toplogl:
                toplogl = logl
                toptree = tree2
        tree = toptree

        # only use visited hash table if all genes are present        
        if ngenes == totalgenes:
            visited2 = visited
        else:
            # otherwise use a new temp hash table
            visited2 = {}
        
        tree, logl = searchExhaustive(conf, distmat2, labels2, 
                                      tree, stree, gene2species, params,
                                      visited=visited2)
            
            
        if logl >= toplogl:
            toplogl = logl
            toptree = tree
        tree = toptree
        
        
        debug()
    
    visited.update(visited2)
    
    return tree, toplogl



def searchExhaustive(conf, distmat, labels, tree, stree, gene2species, params,
                     depth=2, visited=None, topDepth=True):
    if visited == None:
        visited = {}
    
    # find initial logl
    thash = phyloutil.hashTree(tree)
    if thash not in visited:
        setTreeDistances(conf, tree, distmat, labels)
        logl = treeLogLikelihood(conf, tree, stree, 
                                    gene2species, params)
        visited[thash] = [logl, tree.copy(), 1]
        
        drawTreeLogl(tree)
    
    
    debug(" " * (depth*2), "(%d)" % len(visited))
    sys.stdout.flush()
    
    # try all NNI
    # find edges for NNI
    nodes = tree.nodes.values()
    nodes = filter(lambda x: not x.isLeaf() and 
                             x != tree.root, nodes)
    edges = [(node, node.parent) for node in nodes]

    for edge in edges:
        for change in (0,1):
            proposeNni(tree, edge[0], edge[1], change)
            
            tree2 = tree
            #tree2 = phyloutil.reconRoot(tree, stree, gene2species,
            #                            rootby="duploss")
            
            thash = phyloutil.hashTree(tree2)
            if thash not in visited:
                setTreeDistances(conf, tree2, distmat, labels)
                logl = treeLogLikelihood(conf, tree2, stree, 
                                         gene2species, params)
                visited[thash] = [logl, tree2.copy(), 1]
                
                
                # dig deeper
                if depth > 1:
                    searchExhaustive(conf, distmat, labels, 
                                     tree2, stree, gene2species, params,
                                     depth=depth-1, visited=visited,
                                     topDepth=False)
            
            # switch branch back
            proposeNni(tree, edge[0], edge[1], change)
    
    # debug
    if topDepth and isDebug(DEBUG_LOW):
        items = visited.items()
        i = util.argmaxfunc(lambda x: x[1], items)
        
        thash, (logl, tree, count) = items[i]
        
        return tree, logl
    else:
        return None, None


#-------------------------------------------------------------------------------
# Main SINDIR algorithm function
#-------------------------------------------------------------------------------

def sindir(conf, distmat, labels, stree, gene2species, params):
    """Main function for the SINDIR algorithm"""
    
    setDebug(conf["debug"])
    
    trees = []
    logls = []
    tree = None
    visited = {}
    
    util.tic("SINDIR")
    
    # do auto searches
    for search in conf["search"]:

        if search == "greedy":
            tree, logl = searchGreedy(conf, distmat, labels, stree, 
                                      gene2species, params,
                                      visited=visited)
            
        elif search == "mcmc":
            tree, logl = searchMCMC(conf, distmat, labels, stree, 
                                    gene2species, params, initTree=tree,
                                    visited=visited)
        elif search == "exhaustive":
            if tree == None:
                tree = neighborjoin(distmat, labels)
                tree = phyloutil.reconRoot(tree, stree, gene2species)
            
            tree, logl = searchExhaustive(conf, distmat, labels, tree, stree, 
                                          gene2species, params, 
                                          depth=conf["depth"],
                                          visited=visited)
        elif search == "none":
            break
        else:
            raise SindirError("unknown search '%s'" % search)
               
        printVisitedTrees(visited)
        
    
    # eval the user given trees
    for treefile in conf["tree"]:
        tree = treelib.readTree(treefile)
        
        if True: #sum(node.dist for node in tree.nodes.values()) == 0.0: # or True:
            debug("fitting distances")     
            setTreeDistances(conf, tree, distmat, labels)
        else:
            debug("use distances from file")
        logl = treeLogLikelihood(conf, tree, stree, gene2species, params)
        
        thash = phyloutil.hashTree(tree)
        if thash in visited:
            a, b, count = visited[thash]
        else:
            count = 0
        visited[thash] = [logl, tree.copy(), count+1]
        
        if isDebug(DEBUG_LOW):
            debug("\nuser given tree:")
            recon = phyloutil.reconcile(tree, stree, gene2species)
            events = phyloutil.labelEvents(tree, recon)
            drawTreeLogl(tree, events=events)
    
    util.toc()
    
    if len(visited) == 0:
        raise SindirError("No search or tree topologies given")
    
    
    
    # find best tree
    if False:
        errorfactor = 1.3
        minerror = min(x[1].data["error"] for x in visited.itervalues())

        # find all tree with error near minerror
        goodtrees = filter(lambda x: x[1].data["error"] < errorfactor * minerror,
                           visited.itervalues())

        # find best tree as max logl in good trees
        i = util.argmax([x[1].data["logl"] for x in goodtrees])

    if False:
        errorcutoff = conf["maxerror"]
        trees = [x[1] for x in visited.values()]
        errors = [tree.data["error"] for tree in trees]
        

        # find all tree with acceptable error
        goodind = util.find(lambda err: err < errorcutoff, errors)
        if len(goodind) > 0:
            goodtrees = util.mget(trees, goodind)
        else:
            # default to all trees if all errors are high
            debug("WARNING: high error rate in all trees found")
            goodtrees = trees

        # find best tree as max logl in good trees
        i = util.argmax([x.data["logl"] for x in goodtrees])
        return goodtrees[i], goodtrees[i].data["logl"]
    
    
    # return ML tree
    if True:
        trees = [x[1] for x in visited.itervalues()]
        i = util.argmax([x.data["logl"] for x in trees])
        return trees[i], trees[i].data["logl"]
    
    
    if False:
        # find best consensus tree
        items = visited.values()
        items.sort(key=lambda x: x[1].data["logl"], reverse=True)
        mat = [[x[1], x[2]] for x in items[:conf["toptrees"]]]
        trees, counts = zip(* mat)

        phylip.writeBootTrees(conf["out"] + ".trees", trees, counts=counts)
        tree = phylip.consense(trees, counts=counts, verbose=False, args="r\ny")
        return tree, 0



def consensusTree(trees, counts):
    splits = util.Dict(default=0)
    
    genes = util.sort(trees[0].leaveNames())
    
    # count up splits
    for tree, count in zip(trees, counts):
        network = treelib.tree2graph(treelib.unroot(tree))
        splits2 = findSplits(network, util.makeset(tree.leaveNames()))
        
        print len(splits2)
        
        for key, (set1, set2) in splits2.iteritems():
            if len(set1) > len(set2):
                set1, set2 = set2, set1
            splitkey = tuple([int(gene in set1) for gene in genes])
            splits[splitkey] += count
    
    splits = splits.items()
    splits.sort(key=lambda x: x[1], reverse=True)
    
    half = len(trees) / 2.0
    if util.count(lambda x: x[1] >= half, splits):
        debug("consensus exists")
    
    # print splits
    if isDebug(DEBUG_LOW):
        mat = [genes + ["COUNT"]]
        for key, val in splits:
            mat.append(list(key))
            mat[-1].append(val)
        util.printcols(mat, out=DEBUG)
    
    
    
    
    
    return tree, tree.data["logl"]


#
# testing
#
if __name__ == "__main__":
    import StringIO
    
    def floateq(a, b, accuracy=.0001):
        if b - accuracy <= a <= b + accuracy:
            print "pass"
            print a, "==", b
        else:
            print a, "!=", b
            raise Exception("not equal")
        
    
    
    def gene2species(name):
        return name[:1].upper()
    
    
    params = {"A": [4, 2],
              "B": [3, 1]}
              
    conf = {"debug": 0,
            "dupprob": .5,
            "lossprob": 1.0}
    
    
    
    stree = treelib.readTree(StringIO.StringIO("(A, B);"))
    
    
    # test 1
    print "\n\nTest 1"
    tree  = treelib.readTree(StringIO.StringIO("(a:3, b:2);"))
    logl = treeLogLikelihood(conf, tree, stree, gene2species, params, baserate=1)
    
    treelib.drawTreeLens(tree,scale=5)
    floateq(logl, log(stats.normalPdf(3, params["A"]) *
                      stats.normalPdf(2, params["B"])))
    
    
    # test 2
    print "\n\nTest 2"    
    tree  = treelib.readTree(StringIO.StringIO("((a1:2.5, a2:2):1, b:2);"))
    logl = treeLogLikelihood(conf, tree, stree, gene2species, params, baserate=1)
    
    treelib.drawTreeLens(tree,scale=5)
    floateq(logl, log(stats.normalPdf(2.5+1, params["A"])) +
                  log(stats.normalPdf(2+1, params["A"])) -
                  log(1.0 - stats.normalCdf(1, params["A"])) +
                  log(stats.normalPdf(2, params["B"])))


    print "\n\nTest 3"    
    tree  = treelib.readTree(StringIO.StringIO(
                             "(((a1:2.5, a2:2):1, a3:1.5):1.2, b:2);"))
    logl = treeLogLikelihood(conf, tree, stree, gene2species, params, baserate=1)
    
    treelib.drawTreeLens(tree,scale=5)
    floateq(logl, log(stats.normalPdf(2.5+1+1.2, params["A"])) +
                  log(stats.normalPdf(2+1+1.2, params["A"])) -
                  log(1.0 - stats.normalCdf(1+1.2, params["A"])) +
                  log(stats.normalPdf(1.5+1.2, params["A"])) -
                  log(1.0 - stats.normalCdf(1.2, params["A"])) +
                  log(stats.normalPdf(2, params["B"])))    



