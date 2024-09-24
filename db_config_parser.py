from configparser import ConfigParser
import os


def parse_info(filename, section='postgresql'):
    # create a parser
    parser = ConfigParser()
    # read config file
    parser.read(filename)

    # get section, default to postgresql
    section_info = {}
    if parser.has_section(section):
        params = parser.items(section)
        for param in params:
            section_info[param[0]] = param[1]
    else:
        raise Exception('Section {0} not found in the {1} file'.format(section, filename))
    return section_info


def get_conn_str():
    config = os.environ.get('DB_CONFIG', 'database.ini')
    db_info = parse_info(config, 'postgresql')
    hostname = db_info.get("host")
    database = db_info.get("database")
    username = db_info.get("user")
    password = db_info.get("password")
    port = db_info.get("port")
    conn_str = f"postgresql://{username}:{password}@{hostname}:{port}/{database}"
    return conn_str


def security_API_key():
    config = os.environ.get('DB_CONFIG', 'database.ini')
    security_info = parse_info(config, 'security')
    apikey = security_info.get("apikey")
    return apikey


def API_version():
    config = os.environ.get('DB_CONFIG', 'database.ini')
    security_info = parse_info(config, 'security')
    apiversion = security_info.get("apiversion")
    return apiversion


def API_port():
    config = os.environ.get('DB_CONFIG', 'database.ini')
    security_info = parse_info(config, 'security')
    apiversion = security_info.get("apiport")
    return apiversion

def redis_config():
    config = os.environ.get('DB_CONFIG', 'database.ini')
    redis_info = parse_info(config, 'redis')
    return redis_info


def get_xiview_base_url():
    config = os.environ.get('DB_CONFIG', 'database.ini')
    security_info = parse_info(config, 'security')
    xiviewbaseurl = security_info.get("xiviewbaseurl")
    return xiviewbaseurl
