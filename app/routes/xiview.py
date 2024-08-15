import functools
import logging
import logging.config
import struct
import time

import fastapi
import psycopg2
from fastapi import APIRouter, Depends, Request, Response
import orjson
from psycopg2 import sql
from psycopg2.extras import RealDictCursor
from sqlalchemy.orm import session, Session

from models.upload import Upload
from app.routes.shared import get_db_connection, get_most_recent_upload_ids, log_execution_time_async
from index import get_session
from db_config_parser import get_xiview_base_url

xiview_data_router = APIRouter()


class EndpointFilter(logging.Filter):
    """
    Define the filter to stop logging for visualisation endpoint which will be called very frequently
    and log file will be flooded with this endpoint request logs
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return record.args and len(record.args) >= 3 and not str(record.args[2]).__contains__("/data/visualisations/")


logger = logging.getLogger(__name__)
logging.getLogger("uvicorn.access").addFilter(EndpointFilter())


@xiview_data_router.get('/get_xiview_data', tags=["xiVIEW"])
async def get_xiview_data(project, file=None):
    """
    Get the data for the network visualisation.
    URLs have the following structure:
    https: // www.ebi.ac.uk / pride / archive / xiview / network.html?project=PXD020453&file=Cullin_SDA_1pcFDR.mzid
    Users may provide only projects, meaning we need to have an aggregated  view.
    https: // www.ebi.ac.uk / pride / archive / xiview / network.html?project=PXD020453

    :return: json with the data
    """
    logger.info(f"get_xiview_data for {project}, file: {file}")
    most_recent_upload_ids = await get_most_recent_upload_ids(project, file)
    try:
        data_object = await get_data_object(most_recent_upload_ids, project)
    except psycopg2.DatabaseError as e:
        logger.error(e)
        print(e)
        return {"error": "Database error"}, 500
    json_bytes = orjson.dumps(data_object)
    log_json_size(json_bytes, "everything")  # this slows things down a little, comment out later
    return Response(json_bytes, media_type='application/json')


def log_json_size(json_bytes, name):
    json_size_mb = len(json_bytes) / (1024 * 1024)
    logger.info(f"uncompressed size of json {name}: {json_size_mb} Mb")


@xiview_data_router.get('/get_peaklist', tags=["xiVIEW"])
async def get_peaklist(id, sd_ref, upload_id):
    conn = None
    data = {}
    error = None
    try:
        conn = await get_db_connection()
        cur = conn.cursor()
        query = "SELECT intensity, mz FROM spectrum WHERE id = %s AND spectra_data_id = %s AND upload_id = %s"
        cur.execute(query, [id, sd_ref, upload_id])
        resultset = cur.fetchall()[0]
        data["intensity"] = struct.unpack('%sd' % (len(resultset[0]) // 8), resultset[0])
        data["mz"] = struct.unpack('%sd' % (len(resultset[1]) // 8), resultset[1])
        cur.close()
    except (Exception, psycopg2.DatabaseError) as e:
        # logger.error(error)
        error = e
    finally:
        if conn is not None:
            conn.close()
            # logger.debug('Database connection closed.')
        if error is not None:
            raise error
        return data


@xiview_data_router.get('/visualisations/{project_id}', tags=["xiVIEW"])
def visualisations(project_id: str, request: Request, session: Session = Depends(get_session)):
    xiview_base_url = get_xiview_base_url()
    project_detail = session.query(Upload) \
        .filter(Upload.project_id == project_id) \
        .all()
    datasets = []
    processed_filenames = set()
    for record in project_detail:
        filename = record.identification_file_name
        if filename not in processed_filenames:
            datafile = {
                "filename": filename,
                "visualisation": "cross-linking",
                "link": (xiview_base_url + "?project=" + project_id + "&file=" +
                         str(filename))
            }
            datasets.append(datafile)
            processed_filenames.add(filename)

    return datasets


@log_execution_time_async
async def get_data_object(ids, pxid):
    """ Connect to the PostgreSQL database server """
    conn = None
    data = {}
    error = None
    try:
        conn = await get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        data["project"] = await get_pride_api_info(cur, pxid)
        data["meta"] = await get_results_metadata(cur, ids)
        data["matches"] = await get_matches(cur, ids)
        # data["peptides"] = await get_peptides(cur, data["matches"], ids)
        # data["proteins"] = await get_proteins(cur, data["peptides"])
        data["peptides"] = await get_peptides2(cur, ids)
        data["proteins"] = await get_all_proteins(cur, ids)
        cur.close()
    except (Exception, psycopg2.DatabaseError) as e:
        error = e
        logger.error(e)
        raise e
    finally:
        if conn is not None:
            conn.close()
        if error is not None:
            raise error
        return data


@log_execution_time_async
async def get_pride_api_info(cur, pxid):
    """ Get the PRIDE API info for the projects """
    query = """SELECT p.id AS id,
                p.id,
                p.title,
                p.description
                FROM projectdetails p
                WHERE p.project_id = (%s);"""
    cur.execute(query, [pxid])
    return cur.fetchall()


@log_execution_time_async
async def get_results_metadata(cur, ids):
    """ Get the metadata for the results """
    metadata = {}

    # get Upload(s) for each id
    query = """SELECT u.id AS id,
                u.project_id,
                u.identification_file_name,
                u.provider,
                u.audit_collection,
                u.analysis_sample_collection,
                u.bib,
                u.spectra_formats,
                u.contains_crosslinks,
                u.upload_warnings AS warnings
            FROM upload u
            WHERE u.id = ANY(%s);"""
    cur.execute(query, [ids])
    metadata["mzidentml_files"] = cur.fetchall()

    # get analysiscollectionspectrumidentification(s) for each id
    query = """SELECT ac.upload_id,
                ac.spectrum_identification_list_ref,
                ac.spectrum_identification_protocol_ref,
                ac.spectra_data_refs,
                ac.search_database_refs
            FROM analysiscollectionspectrumidentification ac
            WHERE ac.upload_id = ANY(%s);"""
    cur.execute(query, [ids])
    metadata["analysis_collections"] = cur.fetchall()

    # get SpectrumIdentificationProtocol(s) for each id
    query = """SELECT sip.id AS id,
                sip.sip_ref,    
                sip.upload_id,
                sip.frag_tol,
                sip.frag_tol_unit,
                sip.additional_search_params,
                sip.analysis_software,
                sip.threshold
            FROM spectrumidentificationprotocol sip
            WHERE sip.upload_id = ANY(%s);"""
    cur.execute(query, [ids])
    metadata["spectrum_identification_protocols"] = cur.fetchall()

    # spectradata for each id
    query = """SELECT *
            FROM spectradata sd
            WHERE sd.upload_id = ANY(%s);"""
    cur.execute(query, [ids])
    metadata["spectra_data"] = cur.fetchall()

    # enzymes
    query = """SELECT *
            FROM enzyme e
            WHERE e.upload_id = ANY(%s);"""
    cur.execute(query, [ids])
    metadata["enzymes"] = cur.fetchall()

    # search modifications
    try:
        query = """SELECT *
                FROM searchmodification sm
                WHERE sm.upload_id = ANY(%s);"""
        cur.execute(query, [ids])
        metadata["search_modifications"] = cur.fetchall()
    except Exception as e:
        print(e)

    return metadata


@log_execution_time_async
async def get_matches(cur, ids):
    # todo - check whats going on with this rank =1 and pass_threshold = True in mascot data, rank =1 condition seems to speeds things up (but should be redundant)
    # todo - rename 'si' to 'm'
    query = """WITH submodpep AS (SELECT * FROM modifiedpeptide WHERE upload_id = ANY(%s) AND link_site1 > -1)
SELECT si.id AS id, si.pep1_id AS pi1, si.pep2_id AS pi2,
                si.scores AS sc,
                cast (si.upload_id as text) AS si,
                si.calc_mz AS c_mz,
                si.charge_state AS pc_c,
                si.exp_mz AS pc_mz,
                si.spectrum_id AS sp,
                si.spectra_data_id AS sd,
                si.pass_threshold AS p,
                si.rank AS r,
                si.sip_id AS sip                
            FROM match si 
            INNER JOIN submodpep mp1 ON si.upload_id = mp1.upload_id AND si.pep1_id = mp1.id 
            INNER JOIN submodpep mp2 ON si.upload_id = mp2.upload_id AND si.pep2_id = mp2.id 
            WHERE si.upload_id = ANY(%s) 
            AND si.pass_threshold = TRUE 
            AND mp1.link_site1 > -1
            AND mp2.link_site1 > -1;"""
    cur.execute(query, [ids, ids])
    return cur.fetchall()


@log_execution_time_async
async def get_peptides(cur, match_rows, ids):
    search_peptide_ids = {}
    for match_row in match_rows:
        if match_row['si'] in search_peptide_ids:
            peptide_ids = search_peptide_ids[match_row['si']]
        else:
            peptide_ids = set()
            search_peptide_ids[match_row['si']] = peptide_ids
        peptide_ids.add(match_row['pi1'])
        if match_row['pi2'] is not None:
            peptide_ids.add(match_row['pi2'])

    subclauses = []
    for k, v in search_peptide_ids.items():
        pep_id_literals = []
        for pep_id in v:
            pep_id_literals.append(sql.Literal(pep_id))
        joined_pep_ids = sql.SQL(',').join(pep_id_literals)
        subclause = sql.SQL("(mp.upload_id = {} AND id IN ({}))").format(
            sql.Literal(k),
            joined_pep_ids
        )
        subclauses.append(subclause)
    peptide_clause = sql.SQL(" OR ").join(subclauses)

    # make composable sql array from ids
    composableIds = [sql.Literal(id) for id in ids]
    joined_ids = sql.SQL(",").join(composableIds)
    query = sql.SQL("""WITH subpp AS (select * from peptideevidence WHERE upload_id = ANY(ARRAY[{}]))
           SELECT mp.id, cast(mp.upload_id as text) AS u_id,
                mp.base_sequence AS seq,
                array_agg(pp.dbsequence_id) AS prt,
                array_agg(pp.pep_start) AS pos,
                array_agg(pp.is_decoy) AS dec,
                mp.link_site1 AS ls1,
                mp.link_site2 AS ls2,
                mp.mod_accessions as m_as,
                mp.mod_positions as m_ps,
                mp.mod_monoiso_mass_deltas as m_ms,
                mp.crosslinker_modmass as cl_m                     
                    FROM modifiedpeptide AS mp
                    JOIN subpp AS pp
                    ON mp.id = pp.peptide_id AND mp.upload_id = pp.upload_id
                WHERE {}
                GROUP BY mp.id, mp.upload_id, mp.base_sequence;""").format(joined_ids,
        peptide_clause
    )
    # logger.debug(query.as_string(cur))
    cur.execute(query)
    return cur.fetchall()


@log_execution_time_async
async def get_proteins(cur, peptide_rows):
    search_protein_ids = {}
    for peptide_row in peptide_rows:
        if peptide_row['u_id'] in search_protein_ids:
            protein_ids = search_protein_ids[peptide_row['u_id']]
        else:
            protein_ids = set()
            search_protein_ids[peptide_row['u_id']] = protein_ids
        for prot in peptide_row['prt']:
            protein_ids.add(prot)

    subclauses = []
    for k, v in search_protein_ids.items():
        literals = []
        for prot_id in v:
            literals.append(sql.Literal(prot_id))
        joined_literals = sql.SQL(",").join(literals)
        subclause = sql.SQL("(upload_id = {} AND id IN ({}))").format(
            sql.Literal(k),
            joined_literals
        )
        subclauses.append(subclause)

    protein_clause = sql.SQL(" OR ").join(subclauses)
    query = sql.SQL("""SELECT id, name, accession, sequence,
                     cast(upload_id as text) AS search_id, description FROM dbsequence WHERE ({});""").format(
        protein_clause
    )
    # logger.debug(query.as_string(cur))
    cur.execute(query)
    return cur.fetchall()


@log_execution_time_async
@xiview_data_router.get('/get_xiview_matches', tags=["xiVIEW"])
async def get_xiview_matches(project, file=None):
    """
    Get the passing matches.
    URLs have the following structure:
    https: // www.ebi.ac.uk / pride / archive / xiview / get_xiview_matches?project=PXD020453&file=Cullin_SDA_1pcFDR.mzid
    Users may provide only projects, meaning we need to have an aggregated  view.
    https: // www.ebi.ac.uk / pride / archive / xiview / get_xiview_matches?project=PXD020453

    :return: json of the matches
    """
    logger.info(f"get_xiview_matches for {project}, file: {file}")
    most_recent_upload_ids = await get_most_recent_upload_ids(project, file)

    conn = None
    data = {}
    error = None

    try:
        conn = await get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        data = await get_matches(cur, most_recent_upload_ids)
        cur.close()
    except (Exception, psycopg2.DatabaseError) as e:
        logger.error(e)
        return {"error": "Database error"}, 500
    finally:
        if conn is not None:
            conn.close()

    start_time = time.time()
    json_bytes = orjson.dumps(data)
    logger.info(f'matches json dump time: {time.time() - start_time}')
    log_json_size(json_bytes, "matches")  # slows things down a little
    return Response(json_bytes, media_type='application/json')


@log_execution_time_async
@xiview_data_router.get('/get_xiview_peptides', tags=["xiVIEW"])
async def get_xiview_peptides(project, file=None):
    """
    Get all the peptides.
    URLs have the following structure:
    https: // www.ebi.ac.uk / pride / archive / xiview / get_xiview_peptides?project=PXD020453&file=Cullin_SDA_1pcFDR.mzid
    Users may provide only projects, meaning we need to have an aggregated view.
    https: // www.ebi.ac.uk / pride / archive / xiview / get_xiview_peptides?project=PXD020453

    :return: json of the peptides
    """
    logger.info(f"get_xiview_peptides for {project}, file: {file}")
    most_recent_upload_ids = await get_most_recent_upload_ids(project, file)

    conn = None
    data = {}
    error = None

    try:
        conn = await get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        data = await get_all_peptides(cur, most_recent_upload_ids)
        cur.close()
    except (Exception, psycopg2.DatabaseError) as e:
        logger.error(e)
        return {"error": "Database error"}, 500
    finally:
        if conn is not None:
            conn.close()

    start_time = time.time()
    json_bytes = orjson.dumps(data)
    logger.info(f'peptides json dump time: {time.time() - start_time}')
    log_json_size(json_bytes, "peptides")  # slows things down a little
    return Response(json_bytes, media_type='application/json')


@log_execution_time_async
async def get_all_peptides(cur, ids):
    query = """WITH subpp AS (select * from peptideevidence WHERE upload_id = ANY(%s))
           SELECT mp.id,
                cast(mp.upload_id as text) AS u_id,
                mp.base_sequence AS seq,
                array_agg(pp.dbsequence_id) AS prt,
                array_agg(pp.pep_start) AS pos,
                array_agg(pp.is_decoy) AS dec,
                mp.link_site1 AS ls1,
                mp.link_site2 AS ls2,
                mp.mod_accessions as m_as,
                mp.mod_positions as m_ps,
                mp.mod_monoiso_mass_deltas as m_ms,
                mp.crosslinker_modmass as cl_m
                    FROM modifiedpeptide AS mp
                    JOIN subpp AS pp
                    ON mp.id = pp.peptide_id AND mp.upload_id = pp.upload_id
                WHERE mp.upload_id = ANY(%s)
                GROUP BY mp.id, mp.upload_id, mp.base_sequence;"""

    cur.execute(query, [ids, ids])
    return cur.fetchall()

@log_execution_time_async
@xiview_data_router.get('/get_xiview_peptides2', tags=["xiVIEW"])
async def get_xiview_peptides2(project, file=None):
    """
    Get all the peptides.
    URLs have the following structure:
    https: // www.ebi.ac.uk / pride / archive / xiview / get_xiview_peptides?project=PXD020453&file=Cullin_SDA_1pcFDR.mzid
    Users may provide only projects, meaning we need to have an aggregated view.
    https: // www.ebi.ac.uk / pride / archive / xiview / get_xiview_peptides?project=PXD020453

    :return: json of the peptides
    """
    logger.info(f"get_xiview_peptides for {project}, file: {file}")
    most_recent_upload_ids = await get_most_recent_upload_ids(project, file)

    conn = None
    data = {}
    error = None

    try:
        conn = await get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        data = await get_peptides2(cur, most_recent_upload_ids)
        cur.close()
    except (Exception, psycopg2.DatabaseError) as e:
        logger.error(e)
        return {"error": "Database error"}, 500
    finally:
        if conn is not None:
            conn.close()

    start_time = time.time()
    json_bytes = orjson.dumps(data)
    logger.info(f'peptides json dump time: {time.time() - start_time}')
    log_json_size(json_bytes, "peptides")  # slows things down a little
    return Response(json_bytes, media_type='application/json')


@log_execution_time_async
async def get_peptides2(cur, ids):
    query = """with submatch as (select pep1_id, pep2_id, upload_id from match where upload_id = ANY(%s) and pass_threshold = true), 
pep_ids as (select upload_id, pep1_id from submatch  union select upload_id, pep2_id from submatch),
subpp AS (select * from peptideevidence WHERE upload_id = ANY(%s))
select mp.id,
                cast(mp.upload_id as text) AS u_id,
                mp.base_sequence AS seq,
                array_agg(pp.dbsequence_id) AS prt,
                array_agg(pp.pep_start) AS pos,
                array_agg(pp.is_decoy) AS dec,
                mp.link_site1 AS ls1,
                mp.link_site2 AS ls2,
                mp.mod_accessions as m_as,
                mp.mod_positions as m_ps,
                mp.mod_monoiso_mass_deltas as m_ms,
                mp.crosslinker_modmass as cl_m from pep_ids pi
inner join modifiedpeptide mp on mp.upload_id = pi.upload_id and pi.pep1_id = mp.id
                    JOIN subpp AS pp
                    ON mp.upload_id = pp.upload_id AND mp.id = pp.peptide_id 
                    GROUP BY mp.id, mp.upload_id, mp.base_sequence;"""

    cur.execute(query, [ids, ids])
    return cur.fetchall()


@log_execution_time_async
@xiview_data_router.get('/get_xiview_proteins', tags=["xiVIEW"])
async def get_xiview_proteins(project, file=None):
    """
    Get all the proteins.
    URLs have the following structure:
    https: // www.ebi.ac.uk / pride / archive / xiview / get_xiview_proteins?project=PXD020453&file=Cullin_SDA_1pcFDR.mzid
    Users may provide only projects, meaning we need to have an aggregated  view.
    https: // www.ebi.ac.uk / pride / archive / xiview / get_xiview_proteins?project=PXD020453

    :return: json of the proteins
    """
    logger.info(f"get_xiview_proteins for {project}, file: {file}")
    most_recent_upload_ids = await get_most_recent_upload_ids(project, file)

    conn = None
    data = {}
    error = None

    try:
        conn = await get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        data = await get_all_proteins(cur, most_recent_upload_ids)
        cur.close()
    except (Exception, psycopg2.DatabaseError) as e:
        logger.error(e)
        return {"error": "Database error"}, 500
    finally:
        if conn is not None:
            conn.close()

    start_time = time.time()
    json_bytes = orjson.dumps(data)
    logger.info(f'proteins json dump time: {time.time() - start_time}')
    log_json_size(json_bytes, "proteins")  # slows things down a little
    return Response(json_bytes, media_type='application/json')


@log_execution_time_async
async def get_all_proteins(cur, ids):
    query = """SELECT id, name, accession, sequence,
                     cast(upload_id as text) AS search_id, description FROM dbsequence
                     WHERE upload_id = ANY(%s)
                ;"""
    cur.execute(query, [ids])
    return cur.fetchall()
