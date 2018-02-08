""" JSONkeeper

    Minimal web app made for API access to store and retrieve JSON.
"""

import configparser
import firebase_admin
import json
import os
import re
import sys
import uuid
from collections import OrderedDict
from firebase_admin import auth as firebase_auth
from flask import (abort, Flask, jsonify, redirect, render_template, request,
                   Response, url_for)
from flask_sqlalchemy import SQLAlchemy
from pyld import jsonld
from sqlalchemy.sql import func
from werkzeug.exceptions import default_exceptions, HTTPException


def check_config(config):
    """ Check file `config.ini` for problems.

        Return a problem description or False in case everything's ok.
    """

    # Always required sections
    if 'environment' not in config.sections():
        return 'Config file needs a [environment] section.'

    # Always required values
    if not config['environment'].get('db_uri') or \
       not config['environment'].get('server_url'):
        return ('Config section [environment] needs parameters "db_uri" and "s'
               'erver_url".')

    # Activity stream prerequesites
    if 'activity_stream' in config.sections() and \
       len(config['activity_stream'].get('collection_url', '')) > 0:

        # Need to define types
        agt = config['activity_stream'].get('activity_generating_types', '')
        if len(agt) == 0:
            return ('Serving an Activity Stream requires activity_generating_t'
                    'ypes in config section [activity_stream] to be set.')
        # Need to enable @id rewriting
        id_rewrite = False
        if 'json-ld' in config.sections():
            id_rewrite = config['json-ld'].getboolean('id_rewrite')
        if not id_rewrite:
            return ('Serving an Activity Stream requires id_rewrite in config '
                    'section [json-ld] to be turned on.')
        # Defined types need to be rewritten
        agt_list = [t.split() for t in agt.split(',')]
        rwt = config['json-ld'].get('rewrite_types', '')
        rwt_list = [t.split() for t in rwt.split(',')]
        valid = True
        for gen_type in agt_list:
            if not gen_type in rwt_list:
                valid = False
        if not valid:
            return ('Serving an Activity Stream requires all types set for Act'
                    'ivity generation also to be set for JSON-LD @id rewriting'
                    '.')

    # TODO: instead of going through the config twice, once for sanity checking
    #       and later again for extracting values, this function should return
    #       - a boolean to indicate sanity of the config
    #       - an optional message (error message for invalid config)
    #       - a parsed config object that from then on is used
    return False


app = Flask(__name__)

config = configparser.ConfigParser()
if not os.path.exists('config.ini'):
    print('Config file "config.ini" not found.')
    sys.exit(1)
config.read('config.ini')
fail = check_config(config)
if fail:
    print(fail)
    sys.exit(1)

if 'firebase' in config.sections() and \
       'service_account_key_file' in config['firebase']:
    key_file_path = config['firebase']['service_account_key_file']
    cred = firebase_admin.credentials.Certificate(key_file_path)
    firebase_admin.initialize_app(cred)
    USE_FIREBASE = True
else:
    USE_FIREBASE = False

if 'storage_folder' in config['environment']:
    STORE_FOLDER = config['environment']['storage_folder']
else:
    STORE_FOLDER = False
BASE_URL = config['environment']['server_url']
API_PATH = config['environment'].get('custom_api_path', 'api')
AS_COLL_URL = '-'
if 'activity_stream' in config.sections():
    AS_COLL_URL = config['environment'].get('collection_url', '-')
ID_REWRITE = False
if 'json-ld' in config.sections() and \
   config['json-ld'].getboolean('id_rewrite') and \
   len(config['json-ld'].get('rewrite_types', '')) > 0:
    ID_REWRITE = True
    rwt = config['json-ld'].get('rewrite_types', '')
    REWRITE_TYPES = [t.strip() for t in rwt.split(',')]

app.config['SQLALCHEMY_DATABASE_URI'] = config['environment']['db_uri']
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


class JSON_document(db.Model):
    id = db.Column(db.String(64), primary_key=True)
    access_token = db.Column(db.String(255))
    json_string = db.Column(db.UnicodeText())
    created_at = db.Column(db.DateTime(timezone=True),
                           server_default=func.now())
    updated_at = db.Column(db.DateTime(timezone=True),
                           onupdate=func.now())

db.create_all()
jsonld.set_document_loader(jsonld.requests_document_loader(timeout=3))


if STORE_FOLDER and not os.path.exists(STORE_FOLDER):
    """ Make sure the store folder exists if a path is configured.
    """

    os.makedirs(STORE_FOLDER)


for code in default_exceptions.keys():
    """ Make app return exceptions in JSON form. Also add CORS headers.

        Based on http://flask.pocoo.org/snippets/83/ but updated to use
        register_error_handler method.
        Note: this doesn't seem to work for 405 Method Not Allowed in an
              Apache + gnunicorn setup.
    """

    @app.errorhandler(code)
    def make_json_error(error):
        resp = jsonify(message=str(error))
        resp.status_code = (error.code
                            if isinstance(error, HTTPException)
                            else 500)
        return add_CORS_headers(resp)


def acceptable_content_type(request):
    """ Given a request, assess whether or not the content type is acceptable.

        We allow 'application/json' as well as any content type in the form of
        'application/<something>+json'.where <something> is a string of one or
        more characters that can be anything except for the forward slash "/".
    """

    patt = re.compile('^application/([^/]+\+)?json$')
    if patt.match(request.headers.get('Content-Type')):
        return True
    return False


def update_activity_stream(json_string):
    """ If configured, generate 

        
    """

    pass


def handle_incoming_json_ld(json_string, json_id):
    """ If configured, rewrite root level JSON-LD @ids.

        (Special treatment for sc:Range atm -- generalize later if possible.)
    """

    # check JSON-LD validity
    try:
        root_elem = json.loads(json_string, object_pairs_hook=OrderedDict)
        # https://json-ld.org/spec/latest/json-ld-api/#expansion-algorithms
        expanded = jsonld.expand(root_elem)
    except Exception as e:
        return abort(400, 'No valid JSON-LD provided (this can be due to a con'
                          'text that can not be resolved).')

    # rewrite @ids
    id_change = False
    if ID_REWRITE:
        root_elem_types = expanded[0]['@type']
        if len(set(root_elem_types).intersection(set(REWRITE_TYPES))) > 0:
            root_elem['@id'] = '{}{}'.format(BASE_URL,
                                            url_for('api_json_id',
                                                    json_id=json_id))
            # TODO: for Ranges, we need to go deeper
            json_string = json.dumps(root_elem)
            id_change = True

    return json_string, id_change


def _write_json_request_wrapper(request, given_id, access_token):
    """ 1. do request specific things
        2. call _write_json_request_independent
        3. do response specific things
    """

    json_bytes = request.data
    try:
        json_string = json_bytes.decode('utf-8')
        json.loads(json_string)
    except:
        return abort(400, 'No valid JSON provided.')

    if given_id is None:
        json_id = str(uuid.uuid4())
    else:
        json_id = given_id

    is_json_ld = False
    if request.headers.get('Content-Type') == 'application/ld+json':
        is_json_ld = True

    is_new_document = bool(given_id)
    json_string = _write_json_request_independent(json_string, json_id,
                                                  access_token,
                                                  is_new_document, is_json_ld)

    resp = Response(json_string)
    if given_id is None:
        resp.headers['Location'] = url_for('api_json_id', json_id=json_id)
    # TODO: mby change according to what was given? consistency w/ GET?
    resp.headers['Content-Type'] = 'application/json'

    return resp


def _write_json_request_independent(json_string, json_id, access_token,
                                    is_new_document, is_json_ld):
    """ Get JSON or JSON-LD and save it as configured in `config.ini` (as files
        or in a DB.
    """

    id_change = False
    # If we get JSON-LD, examine it and remember if any documents with
    # resolvable @id (for which we might want to generate new Activities in our
    # Activity Stream) will be saved. After saving update the AS.
    if is_json_ld:
        json_string, id_change = handle_incoming_json_ld(json_string, json_id)

    if STORE_FOLDER:
        # If JSON documents are to be stored in files, we need to write to file
        # regardless of given_id's value
        with open('{}/{}'.format(STORE_FOLDER, json_id),
                  'w', encoding='utf-8') as f:
            f.write(json_string)
        json_string = ''

    if not is_new_document:
        # If this is a new JSON document we need to create a database record
        # regardless of STORE_FOLDER's value
        json_doc = JSON_document(id=json_id,
                                 access_token=access_token,
                                 json_string=json_string)
        db.session.add(json_doc)
        db.session.commit()

    if is_new_document and not STORE_FOLDER:
        # If JSON documents are to be stored in the database and this is a PUT
        # request we need to update the database record
        json_doc = JSON_document.query.filter_by(id=json_id).first()
        json_doc.json_string = json_string
        db.session.commit()

    if id_change:
        pass
        # TODO curationactivity stuff here

    return json_string


def write_json(request, given_id, access_token):
    """ Write JSON contents from request to file or DB. Used for POST requests
        (new document) and PUT requests (update document). Dealing with access
        tokens (given or not in case of POST, correct or not in case of PUT)
        should happen *before* this method is called.

        If the parameter `given_id` is set the corresponding JSON document is
        expected to already exist.

        This function calls _write_json_request_wrapper which in turn calls
        _write_json_request_independent. If JSONkeeper performs internal JSON
        document writing it will directly call _write_json_request_independent.
    """

    return _write_json_request_wrapper(request, given_id, access_token)


def CORS_preflight_response(request):
    """ Create a response for CORS preflight requests.
    """

    resp = Response('')
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = ('GET,POST,DELETE,OPTIONS,'
                                                    'PUT')
    # ↓ what will actually be available via Access-Control-Expose-Headers
    allowed_headers = 'Content-Type,Access-Control-Allow-Origin,Location'
    # ↓ if they ask for something specific we just "comply" to make CORS work
    if 'Access-Control-Request-Headers' in request.headers:
        allowed_headers = request.headers.get('Access-Control-Request-Headers')
    resp.headers['Access-Control-Allow-Headers'] = allowed_headers

    return resp, 200


def add_CORS_headers(resp):
    """ Add CORS headers to a response. This is done even for non CORS requests
        to keep the code simple and because it doesn't hurt non CORS requests).
        This method should, however, not be called for CORS preflight requests
        because they need special treatment.
    """

    if type(resp) is str:
        resp = Response(resp)
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Max-Age'] = '600'
    resp.headers['Access-Control-Expose-Headers'] = ('Content-Type,Access-Cont'
                                                     'rol-Allow-Origin,Locatio'
                                                     'n')

    return resp


def get_access_token(request):
    """ Given a request object, return the resulting access token. This can be:
        - a Firebase uid
        - a freely chosen access token (managed by the client)
        - '' (empty string) in case no access token is provided
        - False in case a Firebase ID token could not be verified
    """

    if USE_FIREBASE and 'X-Firebase-ID-Token' in request.headers:
        id_token = request.headers.get('X-Firebase-ID-Token')
        try:
            decoded_token = firebase_auth.verify_id_token(id_token)
            uid = decoded_token['uid']
            access_token = uid
        except:
            access_token = False
    elif 'X-Access-Token' in request.headers:
        access_token = request.headers.get('X-Access-Token')
    else:
        access_token = ''
    return access_token


def get_JSON_string_by_ID(json_id):
    json_string = None

    if STORE_FOLDER:
        json_location = '{}/{}'.format(STORE_FOLDER, json_id)
        if os.path.isfile(json_location):
            with open(json_location, 'r', encoding='utf-8') as f:
                json_string = f.read()
    else:
        json_doc = JSON_document.query.filter_by(id=json_id).first()
        if json_doc:
            json_string = json_doc.json_string

    return json_string


def handle_post_request(request):
    """ Handle request with the purpose of storing a new JSON document.
    """

    access_token = get_access_token(request)
    if access_token is False:
        return abort(403, 'Firebase ID token could not be verified.')

    resp = write_json(request, None, access_token)

    return add_CORS_headers(resp), 201


def handle_get_request(request, json_id):
    """ Handle request with the purpose of retrieving a JSON document.
    """

    json_string = get_JSON_string_by_ID(json_id)

    if json_string:
        resp = Response(json_string)
        resp.headers['Content-Type'] = 'application/json'
        return add_CORS_headers(resp), 200
    else:
        return abort(404, 'JSON document with ID {} not found'.format(json_id))


def handle_put_request(request, json_id):
    """ Handle request with the purpose of updating a JSON document.
    """

    json_string = get_JSON_string_by_ID(json_id)

    if json_string:
        access_token = get_access_token(request)
        if access_token is False:
            return abort(403, 'Firebase ID token could not be verified.')
        json_doc = JSON_document.query.filter_by(id=json_id).first()
        if json_doc.access_token == access_token or \
                json_doc.access_token == '':
            resp = write_json(request, json_id, access_token)
            return add_CORS_headers(resp), 200
        else:
            return abort(403, 'X-Access-Token header value not correct.')
    else:
        return abort(404, 'JSON document with ID {} not found'.format(json_id))


def handle_delete_request(request, json_id):
    """ Handle request with the purpose of deleting a JSON document.
    """

    json_string = get_JSON_string_by_ID(json_id)

    if json_string:
        access_token = get_access_token(request)
        if access_token is False:
            return abort(403, 'Firebase ID token could not be verified.')
        json_doc = JSON_document.query.filter_by(id=json_id).first()
        if json_doc.access_token == access_token or \
                json_doc.access_token == '':
            db.session.delete(json_doc)
            db.session.commit()
            if STORE_FOLDER:
                json_location = '{}/{}'.format(STORE_FOLDER, json_id)
                os.remove(json_location)
            resp = Response('')
            return add_CORS_headers(resp), 200
        else:
            return abort(403, 'X-Access-Token header value not correct.')
    else:
        return abort(404, 'JSON document with ID {} not found'.format(json_id))


@app.route('/')
def index():
    """ Info page. All requests that don't accept application/json are sent
        here.
    """

    if STORE_FOLDER:
        json_files = [f.path for f in os.scandir(STORE_FOLDER) if f.is_file()]
        num_files = len(json_files)
    else:
        num_files = JSON_document.query.count()

    status_msg = 'Storing {} JSON documents.'.format(num_files)

    if request.accept_mimetypes.accept_json:
        resp = jsonify({'message': status_msg})
        return add_CORS_headers(resp), 200
    else:
        resp = render_template('index.html',
                               api_url=url_for('api'),
                               status_msg=status_msg)
        return add_CORS_headers(resp), 200


@app.route('/{}'.format(API_PATH), methods=['GET', 'POST', 'OPTIONS'])
def api():
    """ API endpoint for posting new JSON documents.

        Allow GET access to send human visitors to the info page.
    """

    if request.method == 'OPTIONS':
        return CORS_preflight_response(request)
    elif request.method == 'POST' and \
            request.accept_mimetypes.accept_json and \
            acceptable_content_type(request):
        return handle_post_request(request)
    else:
        resp = redirect(url_for('index'))
        return add_CORS_headers(resp)


# @app.route('/{}'.format(AS_COLL_URL), methods=['GET', 'POST', 'OPTIONS'])
# def activity_stream_collection():
#     """
#     """
# 
#     pass


# @app.route('/{}/<json_id>/range<r_num>'.format(API_PATH),
#            methods=['GET', 'OPTIONS'])
#     """
#     """
# 
# def api_json_id_range(json_id, r_num):
#     # TODO: is this too specialized?
#     pass


@app.route('/{}/<json_id>'.format(API_PATH),
           methods=['GET', 'PUT', 'DELETE', 'OPTIONS'])
def api_json_id(json_id):
    """ API endpoint for retrieving, updating and deleting JSON documents
    """

    if request.method == 'OPTIONS':
        return CORS_preflight_response(request)
    elif request.method == 'GET' and \
            request.accept_mimetypes.accept_json:
        return handle_get_request(request, json_id)
    elif request.method == 'PUT' and \
            request.accept_mimetypes.accept_json and \
            acceptable_content_type(request):
        return handle_put_request(request, json_id)
    elif request.method == 'DELETE':
        return handle_delete_request(request, json_id)
    else:
        resp = redirect(url_for('index'))
        return add_CORS_headers(resp)
