import json
import math
from math import ceil

import psycopg2
from fastapi import APIRouter, Depends, Path, Response, Query
import orjson
from fastapi import APIRouter, Depends
from psycopg2.extras import RealDictCursor
import logging
from sqlalchemy import text
from sqlalchemy.orm import Session
from index import get_session
from typing import List
from enum import Enum
from typing import Annotated

from app.routes.shared import get_db_connection, get_most_recent_upload_ids

pdbdev_router = APIRouter()

app_logger = logging.getLogger(__name__)


@pdbdev_router.get("/projects/{protein_id}", response_model=List[str], tags=["PDB-Dev"])
async def get_projects_by_protein(protein_id: str, session: Session = Depends(get_session)):
    """
     Get the list of all the datasets in PRIDE crosslinking for a given protein.
    """
    project_list_sql = text(
        """select distinct project_id from upload where id in (select upload_id from dbsequence where accession = :query)""")
    try:
        project_list = session.execute(project_list_sql, {"query": protein_id}).fetchall()
        # Convert the list of tuples into a list of strings
        project_list = [row[0] for row in project_list]
        # Convert the list into JSON format
        # project_list_json = json.dumps(project_list)
    except Exception as error:
        app_logger.error(error)
    return project_list


@pdbdev_router.get('/projects/{project_id}/sequences', tags=["PDB-Dev"])
async def sequences(project_id):
    """
    Get all sequences belonging to a project.

    :param project_id: identifier of a project,
        for ProteomeXchange projects this is the PXD****** accession
    :return: JSON object with all dbref id, mzIdentML file it came from and sequences
    """
    logging.info("Fetching sequences (PDBDev API)")
    most_recent_upload_ids = await get_most_recent_upload_ids(project_id)

    conn = None
    mzid_rows = []
    try:
        # connect to the PostgreSQL server and create a cursor
        conn = await get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        sql = """SELECT dbseq.id, u.identification_file_name  as file, dbseq.sequence, dbseq.accession
                    FROM upload AS u
                    JOIN dbsequence AS dbseq ON u.id = dbseq.upload_id
                    INNER JOIN peptideevidence pe ON dbseq.id = pe.dbsequence_id AND dbseq.upload_id = pe.upload_id
                 WHERE u.id = ANY (%s)
                 AND pe.is_decoy = false
                 GROUP by dbseq.id, dbseq.sequence, dbseq.accession, u.identification_file_name;"""
        cur.execute(sql, [most_recent_upload_ids])
        mzid_rows = cur.fetchall()

        logging.info("Successfully fetched sequences")
    except (Exception, psycopg2.DatabaseError) as error:
        logging.error(error)
    finally:
        if conn is not None:
            conn.close()
            logging.info('Database connection closed.')
        return {"data": mzid_rows}


class Threshold(str, Enum):
    passing = "passing"
    all = "all"

    def is_valid_enum(value: str) -> bool:
        try:
            getattr(Threshold, value)
            return True
        except AttributeError:
            return False


@pdbdev_router.get('/projects/{project_id}/residue-pairs/based-on-reported-psm-level/{passing_threshold}',
                   tags=["PDB-Dev"])
async def get_psm_level_residue_pairs(project_id: Annotated[str, Path(...,
                                                                      title="Project ID",
                                                                      pattern="^PXD\d{6}$",
                                                                      example="PXD019437")],
                                      passing_threshold: Annotated[Threshold, Path(...,
                                                                                   title="Threshold",
                                                                                   description="Threshold passing or all the values",
                                                                                   examples={
                                                                                       "Passing": {
                                                                                           "value": "passing",
                                                                                           "description": "passing threshold"
                                                                                       },
                                                                                       "All": {
                                                                                           "value": "all",
                                                                                           "description": "all threshold"
                                                                                       }
                                                                                   })],
                                      page: int = Query(1, description="Page number"),
                                      page_size: int = Query(10, gt=5, lt=10000, description="Number of items per page"),
                                      ):
    """
    Get all residue pairs (based on PSM level data) belonging to a project.

    There will be multiple entries for identifications with
    positional uncertainty of peptide in protein sequences.
    :param project_id: identifier of a project,
    for ProteomeXchange projects this is the PXD****** accession
    :param passing_threshold: valid values: passing, all
        if 'passing' return residue pairs that passed the threshold
        if 'all' return all residue pairs
    :return:
    """
    if not Threshold.is_valid_enum(passing_threshold):
        return f"Invalid value for passing_threshold: {passing_threshold}. " \
               f"Valid values are: passing, all", 400

    most_recent_upload_ids = await get_most_recent_upload_ids(project_id)
    response = {}
    conn = None
    data = {}
    try:
        # connect to the PostgreSQL server and create a cursor
        conn = await get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        sql_values = {
            "upload_ids": tuple(upload_id[0] for upload_id in most_recent_upload_ids),
            "limit": page_size,
            "offset": (page - 1) * page_size
        }

        if passing_threshold.lower() == Threshold.passing:
            sql = """SELECT array_agg(si.id) as match_ids, array_agg(u.identification_file_name) as files, 
            pe1.dbsequence_id as prot1, dbs1.accession as prot1_acc, (pe1.pep_start + mp1.link_site1 - 1) as pos1,
            pe2.dbsequence_id as prot2, dbs2.accession as prot2_acc, (pe2.pep_start + mp2.link_site1 - 1) as pos2
            FROM match si INNER JOIN
            modifiedpeptide mp1 ON si.pep1_id = mp1.id AND si.upload_id = mp1.upload_id INNER JOIN
            peptideevidence pe1 ON mp1.id = pe1.peptide_id AND mp1.upload_id = pe1.upload_id INNER JOIN
            dbsequence dbs1 ON pe1.dbsequence_id = dbs1.id AND pe1.upload_id = dbs1.upload_id INNER JOIN
            modifiedpeptide mp2 ON si.pep2_id = mp2.id AND si.upload_id = mp2.upload_id INNER JOIN
            peptideevidence pe2 ON mp2.id = pe2.peptide_id AND mp2.upload_id = pe2.upload_id INNER JOIN
            dbsequence dbs2 ON pe2.dbsequence_id = dbs2.id AND pe2.upload_id = dbs2.upload_id INNER JOIN
            upload u on u.id = si.upload_id
            WHERE u.id IN %(upload_ids)s AND mp1.link_site1 > 0 AND mp2.link_site1 > 0 AND pe1.is_decoy = false AND pe2.is_decoy = false
            AND si.pass_threshold = true
            GROUP BY pe1.dbsequence_id , dbs1.accession, (pe1.pep_start + mp1.link_site1 - 1), pe2.dbsequence_id, dbs2.accession , (pe2.pep_start + mp2.link_site1 - 1)
            ORDER BY pe1.dbsequence_id , (pe1.pep_start + mp1.link_site1 - 1), pe2.dbsequence_id, (pe2.pep_start + mp2.link_site1 - 1)
            LIMIT %(limit)s OFFSET %(offset)s;"""
        else:
            sql = """SELECT array_agg(si.id) as match_ids, array_agg(u.identification_file_name) as files,
            pe1.dbsequence_id as prot1, dbs1.accession as prot1_acc, (pe1.pep_start + mp1.link_site1 - 1) as pos1,
            pe2.dbsequence_id as prot2, dbs2.accession as prot2_acc, (pe2.pep_start + mp2.link_site1 - 1) as pos2
            FROM match si INNER JOIN
            modifiedpeptide mp1 ON si.pep1_id = mp1.id AND si.upload_id = mp1.upload_id INNER JOIN
            peptideevidence pe1 ON mp1.id = pe1.peptide_id AND mp1.upload_id = pe1.upload_id INNER JOIN
            dbsequence dbs1 ON pe1.dbsequence_id = dbs1.id AND pe1.upload_id = dbs1.upload_id INNER JOIN
            modifiedpeptide mp2 ON si.pep2_id = mp2.id AND si.upload_id = mp2.upload_id INNER JOIN
            peptideevidence pe2 ON mp2.id = pe2.peptide_id AND mp2.upload_id = pe2.upload_id INNER JOIN
            dbsequence dbs2 ON pe2.dbsequence_id = dbs2.id AND pe2.upload_id = dbs2.upload_id INNER JOIN
            upload u on u.id = si.upload_id
            WHERE u.id IN %(upload_ids)s AND mp1.link_site1 > 0 AND mp2.link_site1 > 0 AND pe1.is_decoy = false AND pe2.is_decoy = false
            GROUP BY pe1.dbsequence_id , dbs1.accession, (pe1.pep_start + mp1.link_site1 - 1), pe2.dbsequence_id, dbs2.accession , (pe2.pep_start + mp2.link_site1 - 1)
            ORDER BY pe1.dbsequence_id , (pe1.pep_start + mp1.link_site1 - 1), pe2.dbsequence_id, (pe2.pep_start + mp2.link_site1 - 1)
            LIMIT %(limit)s OFFSET %(offset)s;"""

        if passing_threshold.lower() == Threshold.passing:
            count_sql = """SELECT count(*) FROM (SELECT array_agg(si.id) as match_ids, array_agg(u.identification_file_name) as files, 
            pe1.dbsequence_id as prot1, dbs1.accession as prot1_acc, (pe1.pep_start + mp1.link_site1 - 1) as pos1,
            pe2.dbsequence_id as prot2, dbs2.accession as prot2_acc, (pe2.pep_start + mp2.link_site1 - 1) as pos2
            FROM match si INNER JOIN
            modifiedpeptide mp1 ON si.pep1_id = mp1.id AND si.upload_id = mp1.upload_id INNER JOIN
            peptideevidence pe1 ON mp1.id = pe1.peptide_id AND mp1.upload_id = pe1.upload_id INNER JOIN
            dbsequence dbs1 ON pe1.dbsequence_id = dbs1.id AND pe1.upload_id = dbs1.upload_id INNER JOIN
            modifiedpeptide mp2 ON si.pep2_id = mp2.id AND si.upload_id = mp2.upload_id INNER JOIN
            peptideevidence pe2 ON mp2.id = pe2.peptide_id AND mp2.upload_id = pe2.upload_id INNER JOIN
            dbsequence dbs2 ON pe2.dbsequence_id = dbs2.id AND pe2.upload_id = dbs2.upload_id INNER JOIN
            upload u on u.id = si.upload_id
            WHERE u.id IN %(upload_ids)s AND mp1.link_site1 > 0 AND mp2.link_site1 > 0 AND pe1.is_decoy = false AND pe2.is_decoy = false
            AND si.pass_threshold = true
            GROUP BY pe1.dbsequence_id , dbs1.accession, (pe1.pep_start + mp1.link_site1 - 1), pe2.dbsequence_id, dbs2.accession , (pe2.pep_start + mp2.link_site1 - 1)
            ) as count;"""
        else:
            count_sql = """SELECT count(*) FROM (SELECT array_agg(si.id) as match_ids, array_agg(u.identification_file_name) as files,
            pe1.dbsequence_id as prot1, dbs1.accession as prot1_acc, (pe1.pep_start + mp1.link_site1 - 1) as pos1,
            pe2.dbsequence_id as prot2, dbs2.accession as prot2_acc, (pe2.pep_start + mp2.link_site1 - 1) as pos2
            FROM match si INNER JOIN
            modifiedpeptide mp1 ON si.pep1_id = mp1.id AND si.upload_id = mp1.upload_id INNER JOIN
            peptideevidence pe1 ON mp1.id = pe1.peptide_id AND mp1.upload_id = pe1.upload_id INNER JOIN
            dbsequence dbs1 ON pe1.dbsequence_id = dbs1.id AND pe1.upload_id = dbs1.upload_id INNER JOIN
            modifiedpeptide mp2 ON si.pep2_id = mp2.id AND si.upload_id = mp2.upload_id INNER JOIN
            peptideevidence pe2 ON mp2.id = pe2.peptide_id AND mp2.upload_id = pe2.upload_id INNER JOIN
            dbsequence dbs2 ON pe2.dbsequence_id = dbs2.id AND pe2.upload_id = dbs2.upload_id INNER JOIN
            upload u on u.id = si.upload_id
            WHERE u.id IN %(upload_ids)s AND mp1.link_site1 > 0 AND mp2.link_site1 > 0 AND pe1.is_decoy = false AND pe2.is_decoy = false
            GROUP BY pe1.dbsequence_id , dbs1.accession, (pe1.pep_start + mp1.link_site1 - 1), pe2.dbsequence_id, dbs2.accession , (pe2.pep_start + mp2.link_site1 - 1)
            ) as count;"""

        cur.execute(sql, sql_values)
        mzid_rows = cur.fetchall()
        data["data"] = mzid_rows

        # Perform a COUNT query to get the total number of elements
        cur.execute(count_sql, sql_values)
        total_elements = cur.fetchone()["count"]

        # Calculate the total pages based on the page size and total elements
        total_pages = math.ceil(total_elements / page_size)

        response = {
            "data": data["data"],
            "page": {
                "page_no": page,
                "page_size": page_size,
                "total_elements": total_elements,
                "total_pages": total_pages
            }
        }

        print("finished")
        # close the communication with the PostgreSQL
        cur.close()
    except (Exception, psycopg2.DatabaseError) as error:
        print(error)
    finally:
        if conn is not None:
            conn.close()
            print('Database connection closed.')
    return Response(orjson.dumps(response), media_type='application/json')


#
# @pdb_dev_router.get('/projects/{project_id}/residue-pairs/reported')
# def get_reported_residue_pairs(project_id):
#     """
#     Get all residue-pairs reported for a project
#     from the ProteinDetectionList element(s).
#
#     :param project_id: identifier of a project,
#         for ProteomeXchange projects this is the PXD****** accession
#     :return:
#     """
#     return "Not Implemented", 501


@pdbdev_router.get('/projects/{project_id}/reported-thresholds', tags=["PDB-Dev"])
async def get_reported_thresholds(project_id):
    """
    Get all reported thresholds for a project.

    :param project_id: identifier of a project,
        for ProteomeXchange projects this is the PXD****** accession
    :return:
    """
    return "Not Implemented", 501
