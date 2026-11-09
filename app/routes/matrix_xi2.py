"""
Data for the XL-MS adjacency matrix viewer (student project), read from the xi2 schema.

Two endpoints, mirroring the viewer's two input files:
  /matches   - one row per top-ranking crosslinked CSM, shaped like the viewer's CSV input
               (Protein1, Protein2, Score, Decoy1, Decoy2, SeqPos1, SeqPos2)
  /sequences - {accession: sequence} for the target proteins those matches touch (its FASTA input)
"""
import logging
import random
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import orjson
from fastapi import APIRouter, Query, Response

from app.routes.shared import execute_query
from app.routes.xiview_xi2 import _parse_resultset_ids

matrix_xi2_router = APIRouter(include_in_schema=False)

logger = logging.getLogger(__name__)

# (search_id, peptide_id) -> [(accession, start, is_decoy), ...]
PeptidePositions = Dict[Tuple[str, int], List[Tuple[str, int, bool]]]


def _sample_matches(matches: List[dict], sample: Optional[int], seed: Optional[int]) -> List[dict]:
    """
    At most `sample` matches, picked at random. Sorted by id first so a given seed picks
    the same subset whatever order the database returned the rows in.
    """
    if sample is None or sample >= len(matches):
        return matches
    ordered = sorted(matches, key=lambda m: (str(m["search_id"]), str(m["id"])))
    return random.Random(seed).sample(ordered, sample)


def _matrix_rows(matches: List[dict], pep_positions: PeptidePositions) -> List[dict]:
    """
    Join matches with their peptides' protein positions into the viewer's CSV row shape.

    Residue position = peptide start + link site - 1 (both 1-based, as in xiVIEW).
    A peptide found in several places gives ';'-joined accessions/positions, like xiFDR output;
    the viewer skips such ambiguous rows. A side is a decoy if any of its proteins is.
    """
    rows = []
    for m in matches:
        sides = []
        for pep_id, site in ((m['pep1_id'], m['s1']), (m['pep2_id'], m['s2'])):
            positions = pep_positions.get((str(m['search_id']), pep_id), [])
            sides.append((
                ';'.join(acc for acc, _, _ in positions),
                ';'.join(str(start + site - 1) for _, start, _ in positions),
                any(decoy for _, _, decoy in positions),
            ))
        (p1, pos1, d1), (p2, pos2, d2) = sides
        if not p1 or not p2:
            continue  # peptide without a protein position - nothing to place in the matrix
        rows.append({
            "Protein1": p1, "Protein2": p2, "Score": m['sc'],
            "Decoy1": d1, "Decoy2": d2, "SeqPos1": pos1, "SeqPos2": pos2,
        })
    return rows


async def _main_score_index(resultset_ids: List[str]) -> int:
    query = """SELECT sn.score_id AS score_index
                FROM scorename AS sn
                WHERE sn.resultset_id = ANY($1::uuid[]) AND sn.primary_score = TRUE
                LIMIT 1;"""
    main_score = await execute_query(query, [resultset_ids], fetch_one=True)
    return main_score['score_index'] if main_score else 0


@matrix_xi2_router.get('/matches', tags=["Matrix"])
async def get_matrix_matches(uuid: List[str] = Query(...),
                             min_score: Optional[float] = None,
                             sample: Optional[int] = Query(None, ge=1),
                             seed: Optional[int] = None):
    """
    Top-ranking crosslinked matches for the given resultset UUIDs, as matrix viewer rows.

    - min_score: only matches with primary score >= this
    - sample: at most this many matches, chosen at random (after the score cutoff)
    - seed: makes the sample reproducible
    """
    logger.info(f"get_matrix_matches for {uuid} (min_score={min_score}, sample={sample}, seed={seed})")
    resultset_ids = _parse_resultset_ids(uuid)
    score_index = await _main_score_index(resultset_ids)

    match_query = """SELECT m.id, m.search_id, m.pep1_id, m.pep2_id,
                        COALESCE(rm.site1, m.site1) AS s1,
                        COALESCE(rm.site2, m.site2) AS s2,
                        rm.scores[$1 + array_lower(rm.scores, 1)] AS sc
                    FROM ResultMatch AS rm
                        JOIN match AS m ON rm.search_id = m.search_id AND rm.match_id = m.id
                    WHERE rm.resultset_id = ANY($2::uuid[])
                        AND m.site1 > 0 AND m.site2 > 0
                        AND rm.top_ranking = TRUE
                        AND ($3::float8 IS NULL OR rm.scores[$1 + array_lower(rm.scores, 1)] >= $3);"""
    t0 = time.time()
    matches = [dict(r) for r in await execute_query(match_query, [score_index, resultset_ids, min_score])]
    matches = _sample_matches(matches, sample, seed)
    logger.info(f"get_matrix_matches: {len(matches)} matches in {time.time()-t0:.2f}s")

    # protein positions of just the peptides those matches use
    search_ids, pep_ids = [], []
    for key in {(m['search_id'], p) for m in matches for p in (m['pep1_id'], m['pep2_id'])}:
        search_ids.append(key[0])
        pep_ids.append(key[1])
    position_query = """SELECT pp.search_id, pp.mod_pep_id, p.accession, pp.start, p.is_decoy
                        FROM unnest($1::uuid[], $2::int[]) AS pi(search_id, pep_id)
                            JOIN peptideposition pp ON pp.search_id = pi.search_id AND pp.mod_pep_id = pi.pep_id
                            JOIN protein p ON p.search_id = pp.search_id AND p.id = pp.protein_id
                        ORDER BY pp.start, p.accession;"""
    pep_positions: PeptidePositions = defaultdict(list)
    for r in await execute_query(position_query, [search_ids, pep_ids]):
        pep_positions[(str(r['search_id']), r['mod_pep_id'])].append((r['accession'], r['start'], r['is_decoy']))

    rows = _matrix_rows(matches, pep_positions)
    logger.info(f"get_matrix_matches: {len(rows)} rows in {time.time()-t0:.2f}s")
    return Response(content=orjson.dumps(rows), media_type='application/json')


@matrix_xi2_router.get('/sequences', tags=["Matrix"])
async def get_matrix_sequences(uuid: List[str] = Query(...)):
    """
    {accession: sequence} for the target proteins referenced by top-ranking crosslinked matches.
    Decoys are left out, as in a FASTA file, so the viewer doesn't fill in decoy residues.
    """
    logger.info(f"get_matrix_sequences for {uuid}")
    query = """WITH submatch AS (
                    SELECT m.pep1_id, m.pep2_id, m.search_id
                    FROM ResultMatch rm
                    JOIN match m ON rm.search_id = m.search_id AND rm.match_id = m.id
                    WHERE rm.resultset_id = ANY($1::uuid[])
                      AND m.site1 > 0 AND m.site2 > 0
                      AND rm.top_ranking = TRUE
                ),
                pep_ids AS (
                    SELECT search_id, pep1_id AS pep_id FROM submatch
                    UNION
                    SELECT search_id, pep2_id FROM submatch
                )
                SELECT DISTINCT p.accession, p.sequence
                FROM pep_ids pi
                JOIN peptideposition pp ON pp.search_id = pi.search_id AND pp.mod_pep_id = pi.pep_id
                JOIN protein p ON p.search_id = pp.search_id AND p.id = pp.protein_id
                WHERE NOT p.is_decoy;"""
    records = await execute_query(query, [_parse_resultset_ids(uuid)])
    sequences = {r['accession']: (r['sequence'] or '').upper() for r in records}
    return Response(content=orjson.dumps(sequences), media_type='application/json')
