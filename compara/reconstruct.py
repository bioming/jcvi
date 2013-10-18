#!/usr/bin/env python
# -*- coding: UTF-8 -*-

"""
From synteny blocks, reconstruct ancestral order by interleaving the genes in
between the anchors. This is the bottom-up method used first in Bowers (2003),
and in Tang (2010), to reconstruct pre-alpha and pre-rho order, respectively.
"""

import sys
import logging

from math import sqrt
from itertools import izip_longest

from jcvi.compara.synteny import AnchorFile, check_beds
from jcvi.formats.bed import Bed
from jcvi.apps.base import OptionParser, ActionDispatcher, debug
debug()


def main():

    actions = (
        ('collinear', 'reduce synteny blocks to strictly collinear'),
        ('zipbed', 'build ancestral contig from collinear blocks'),
        ('pairs', 'convert anchorsfile to pairsfile'),
        # Sankoff-Zheng reconstruction
        ('adjgraph', 'construct adjacency graph'),
            )
    p = ActionDispatcher(actions)
    p.dispatch(globals())


def adjgraph(args):
    """
    %prog adjgraph adjacency.txt subgraph.txt

    Construct adjacency graph for graphviz. The file may look like sample below.
    The lines with numbers are chromosomes with gene order information.

    genome 0
    chr 0
    -1 -13 -16 3 4 -6126 -5 17 -6 7 18 5357 8 -5358 5359 -9 -10 -11 5362 5360
    chr 1
    138 6133 -5387 144 -6132 -139 140 141 146 -147 6134 145 -170 -142 -143
    """
    import pygraphviz as pgv
    from jcvi.utils.iter import pairwise
    from jcvi.formats.base import SetFile

    p = OptionParser(adjgraph.__doc__)
    opts, args = p.parse_args(args)

    if len(args) != 2:
        sys.exit(not p.print_help())

    infile, subgraph = args
    subgraph = SetFile(subgraph)
    subgraph = set(x.strip("-") for x in subgraph)

    G = pgv.AGraph(strict=False)  # allow multi-edge
    SG = pgv.AGraph(strict=False)

    palette = ("green", "magenta", "tomato", "peachpuff")
    fp = open(infile)
    genome_id = -1
    key = 0
    for row in fp:
        if row.strip() == "":
            continue

        atoms = row.split()
        tag = atoms[0]
        if tag in ("ChrNumber", "chr"):
            continue

        if tag == "genome":
            genome_id += 1
            gcolor = palette[genome_id]
            continue

        nodeseq = []
        for p in atoms:
            np = p.strip("-")
            nodeL, nodeR = np + "L", np + "R"
            if p[0] == "-":  # negative strand
                nodeseq += [nodeR, nodeL]
            else:
                nodeseq += [nodeL, nodeR]

        for a, b in pairwise(nodeseq):
            G.add_edge(a, b, key, color=gcolor)
            key += 1

            na, nb = a[:-1], b[:-1]
            if na not in subgraph and nb not in subgraph:
                continue

            SG.add_edge(a, b, key, color=gcolor)

    G.graph_attr.update(dpi="300")

    fw = open("graph.dot", "w")
    G.write(fw)
    fw.close()

    fw = open("subgraph.dot", "w")
    SG.write(fw)
    fw.close()


def pairs(args):
    """
    %prog pairs anchorsfile prefix

    Convert anchorsfile to pairsfile.
    """
    p = OptionParser(pairs.__doc__)
    opts, args = p.parse_args(args)

    if len(args) != 2:
        sys.exit(not p.print_help())

    anchorfile, prefix = args
    outfile = prefix + ".pairs"
    fw = open(outfile, "w")

    af = AnchorFile(anchorfile)
    blocks = af.blocks
    pad = len(str(len(blocks)))
    npairs = 0
    for i, block in enumerate(blocks):
        block_id = "{0}{1:0{2}d}".format(prefix, i + 1, pad)
        lines = []
        for q, s, score in block:
            npairs += 1
            score = score.replace('L', '')
            lines.append("\t".join((q, s, score, block_id)))
        print >> fw, "\n".join(sorted(lines))

    fw.close()
    logging.debug("A total of {0} pairs written to `{1}`.".\
                    format(npairs, outfile))


def interleave_pairs(pairs):
    a, b = pairs[0]
    yield a
    yield b
    for c, d in pairs[1:]:
        assert a < c
        xx = range(a + 1, c)
        yy = range(b + 1, d) if b < d else range(b - 1, d, -1)
        for x, y in izip_longest(xx, yy):
            if x:
                yield x
            if y:
                yield y
        a, b = c, d
        yield a
        yield b


def zipbed(args):
    """
    %prog zipbed species.bed collinear.anchors

    Build ancestral contig from collinear blocks. For example, to build pre-rho
    order, use `zipbed rice.bed rice.rice.1x1.collinear.anchors`. The algorithms
    proceeds by interleaving the genes together.
    """
    p = OptionParser(zipbed.__doc__)
    p.add_option("--prefix", default="b",
                 help="Prefix for the new seqid [default: %default]")
    opts, args = p.parse_args(args)

    if len(args) != 2:
        sys.exit(not p.print_help())

    bedfile, anchorfile = args
    prefix = opts.prefix
    bed = Bed(bedfile)
    order = bed.order
    newbedfile = prefix + ".bed"
    fw = open(newbedfile, "w")

    af = AnchorFile(anchorfile)
    blocks = af.blocks
    pad = len(str(len(blocks)))
    for i, block in enumerate(blocks):
        block_id = "{0}{1:0{2}d}".format(prefix, i + 1, pad)
        pairs = []
        for q, s, score in block:
            qi, q = order[q]
            si, s = order[s]
            pairs.append((qi, si))
        newbed = list(interleave_pairs(pairs))
        for i, b in enumerate(newbed):
            accn = bed[b].accn
            print >> fw, "\t".join(str(x) for x in (block_id, i, i + 1, accn))

    logging.debug("Reconstructed bedfile written to `{0}`.".format(newbedfile))


# Non-linear transformation of anchor scores
score_convert = lambda x: int(sqrt(x))


def get_collinear(block):
    # block contains (gene a, gene b, score)
    asc_score, asc_chain = print_chain(block)
    desc_score, desc_chain = print_chain(block, ascending=False)
    return asc_chain if asc_score > desc_score else desc_chain


def print_chain(block, ascending=True):

    scope = 50  # reduce search complexity
    if not ascending:
        block = [(a, -b, c) for (a, b, c) in block]

    block.sort()
    bsize = len(block)
    fromm = [-1] * bsize
    scores = [score_convert(c) for (a, b, c) in block]

    for i, (a, b, c) in enumerate(block):
        for j in xrange(i + 1, i + scope):
            if j >= bsize:
                break

            d, e, f = block[j]

            # Ensure strictly collinear
            if d == a or b >= e:
                continue

            this_score = scores[i] + score_convert(f)
            if this_score > scores[j]:
                fromm[j] = i
                scores[j] = this_score

    scoresfromm = zip(scores, fromm)
    maxchain = max(scoresfromm)
    chainscore, chainend = maxchain
    solution = [scoresfromm.index(maxchain), chainend]
    last = chainend
    while True:
        _last = fromm[last]
        if _last == -1:
            break
        last = _last
        solution.append(last)

    solution.reverse()
    solution = [block[x] for x in solution]
    if not ascending:
        solution = [(a, -b, c) for (a, b, c) in solution]
    return chainscore, solution


def collinear(args):
    """
    %prog collinear a.b.anchors

    Reduce synteny blocks to strictly collinear, use dynamic programming in a
    procedure similar to DAGchainer.
    """
    p = OptionParser(collinear.__doc__)
    p.set_beds()

    opts, args = p.parse_args(args)

    if len(args) != 1:
        sys.exit(not p.print_help())

    anchorfile, = args
    qbed, sbed, qorder, sorder, is_self = check_beds(anchorfile, p, opts)

    af = AnchorFile(anchorfile)
    newanchorfile = anchorfile.rsplit(".", 1)[0] + ".collinear.anchors"
    fw = open(newanchorfile, "w")

    blocks = af.blocks
    for block in blocks:
        print >> fw, "#" * 3
        iblock = []
        for q, s, score in block:
            qi, q = qorder[q]
            si, s = sorder[s]
            score = int(long(score))
            iblock.append([qi, si, score])

        block = get_collinear(iblock)

        for q, s, score in block:
            q = qbed[q].accn
            s = sbed[s].accn
            print >> fw, "\t".join((q, s, str(score)))

    fw.close()


if __name__ == '__main__':
    main()