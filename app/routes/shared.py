import asyncpg
import functools
import json
import logging
import re
import os
import orjson
import logging.config
import time

from configparser import ConfigParser
from fastapi import status, HTTPException, Security, Response
from fastapi.security import APIKeyHeader
from typing import Optional, List, Any

from db_config_parser import security_API_key

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def log_execution_time_async(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        result = await func(*args, **kwargs)
        end_time = time.time()
        execution_time = end_time - start_time
        logging.info(f'"{func.__name__}" executed in {execution_time:.2f} seconds')
        return result
    return wrapper


def log_json_size(json_bytes, name):
    json_size_mb = len(json_bytes) / (1024 * 1024)
    logger.info(f"uncompressed size of json {name}: {json_size_mb} Mb")

# @log_execution_time_async
async def get_most_recent_upload_ids(pxid, file=None):

    """
    Get the most recent upload ids for a project/file.

    :param pxid: identifier of a project,
        for ProteomeXchange projects this is the PXD****** accession
    :param file: name of the file
    :return: upload ids
    """
    if file:
        filename_clean = re.sub(r'[^0-9a-zA-Z-]+', '-', file)
        query = """SELECT id FROM upload 
                WHERE project_id = %s AND identification_file_name_clean = %s
                ORDER BY upload_time DESC LIMIT 1;"""
        upload_ids = await execute_query(query, [pxid, filename_clean], fetch_one=True)
    else:
        query = """SELECT u.id
                    FROM upload u
                    where u.upload_time = 
                        (select max(upload_time) from upload 
                        where project_id = u.project_id 
                        and identification_file_name = u.identification_file_name )
                    and u.project_id = $1;"""
        upload_ids = await execute_query(query, [pxid], fetch_one=True)
    if upload_ids is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No upload found for the given project/file")
    return upload_ids


async def get_db_connection():
    config = os.environ.get('DB_CONFIG', 'database.ini')

    # https://www.postgresqltutorial.com/postgresql-python/connect/
    async def parse_database_info(filename, section='postgresql'):
        # create a parser
        parser = ConfigParser()
        # read config file
        parser.read(filename)

        # get section, default to postgresql
        db = {}
        if parser.has_section(section):
            params = parser.items(section)
            for param in params:
                db[param[0]] = param[1]
        else:
            raise Exception('Section {0} not found in the {1} file'.format(section, filename))

        return db

    db_info = await parse_database_info(config)
    conn = await asyncpg.connect(**db_info)
    await conn.set_type_codec(
        'json',
        encoder=json.dumps,
        decoder=json.loads,
        schema='pg_catalog'
    )
    return conn


def get_api_key(key: str = Security(api_key_header)) -> str:

    if key == security_API_key():
        return key
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API Key",
    )


async def execute_query(query: str, params: Optional[List[Any]] = None, fetch_one: bool = False):
    """
    Execute a query and return the result
    :param query: the query to execute
    :param params: the parameters to pass to the query
    :param fetch_one: whether to fetch one result or all
    :return: the result of the query
    """
    conn = None
    try:
        # Get the connection without using 'async with'
        conn = await get_db_connection()
        # Execute the query directly on conn without a cursor
        if fetch_one:
            result = await conn.fetchrow(query, *params)
        else:
            result = await conn.fetch(query, *params)
        # No explicit commit needed for asyncpg; auto-commits for DML queries
        # it's all SELECT queries anyway, so no need for commit - cc
        return result

    except Exception as e:
        logging.error(f"Database operation failed: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database operation failed")

    finally:
        # Close the connection explicitly
        if conn:
            await conn.close()

async def fetch_json_response(query, params):
    records = await execute_query(query, params)
    records_list = [dict(record) for record in records]
    return Response(orjson.dumps(records_list), media_type='application/json')
