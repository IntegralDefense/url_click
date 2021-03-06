#!/usr/bin/env python3

import os
import sys
import time
import json
import subprocess
import logging, logging.config

from configparser import ConfigParser
from datetime import datetime, timedelta


CONFIG = None
HOME_DIR = os.path.realpath(os.path.dirname(__file__))
os.chdir(HOME_DIR)

# load lib/ onto the python path
sys.path.append('lib')

from saq.client import Alert
from cloudphishlib import cloudphish

''' MAYBE: remove subprocess, and use splunklib to perform the splunk search '''
def search_splunk(config_path, search_path):
    logging.info('Searching for CB command line URLs in Splunk with search: {}'.format(search_path))

    clicks = []

    start_time = None
    start_time_file = os.path.join(HOME_DIR, 'var', 'last_search_time')
    if not os.path.exists(os.path.join(start_time_file)):
        start_time = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        with open(start_time_file, 'w') as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    else:
        try:
            with open(start_time_file, 'r') as f:
                start_time = f.read()
            datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
            with open(start_time_file, 'w') as f:
                f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        except Exception as e:
            logging.error(str(e))

    if not start_time:
        # first run or Exception logged above
        start_time = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    
    splunk_search_command_text = "/opt/splunklib/splunk -i -s '{}' -c '{}' --search-file '{}' --json".format(start_time, config_path, search_path)
    try:
        logging.debug("Searching splunk with: '{}'".format(splunk_search_command_text))
        results = subprocess.check_output(['/opt/splunklib/splunk.py', '-i', '-s', start_time, '-c', config_path, '--search-file', search_path, '--json']).decode('utf-8')
    except Exception as e:
        logging.exception('Unable to query Splunk: {}'.format(str(e)))
        sys.exit(1)

    # Try converting the results to JSON.
    try:
        j = json.loads(results)
    except:
        logging.exception('Unable to convert Splunk results to JSON.')

    # build our data structure
    for cb_proc in j['result']:

        details = {'url': cb_proc['clicked_url'],
                   'hostname': cb_proc['computer_name'],
                   'process_guid': cb_proc['process_guid'],
                   'company': cb_proc['company'],
                   'domain': cb_proc['dest_nt_domain'],
                   'user': cb_proc['user']}
        
        clicks.append(details)

    return clicks


def create_ace_alert(click):
    alert = Alert(
        tool='url_click',
        tool_instance='Cb cmdline URL Cloudphish checker',
        alert_type='splunk - cb - cloudphish',
        desc='CB URL Click - Cloudphish Detection',
        event_time=time.strftime("%Y-%m-%d %H:%M:%S"),
        details=None,
        name='URL Click',
        company_name=CONFIG.get('ace', 'company_name'),
        company_id=CONFIG['ace'].getint('company_id'))

    tags = ['cloudphish_detection','cb_cmdline']
    for tag in tags:
        alert.add_tag(tag)

    alert.add_observable('process_guid', click['process_guid'])
    alert.add_observable('hostname', click['hostname'])
    alert.add_observable('user', click['user'])
    alert.add_observable('url', click['url'])

    try:
        logging.info("submitting alert {}".format(alert.description))
        alert.submit(CONFIG['ace']['uri'], CONFIG['ace']['key'])
    except Exception as e:
        logging.error("unable to submit alert {}: {}".format(alert, str(e)))

    return


def check_cloudphish(clicks):
    cp = cloudphish()

    total_clicks = len(clicks)
   
    while clicks:
        analyzed_clicks = []
        clicks_to_process = len(clicks)
        counter = 0
        for click in clicks:
            result = cp.submit(click['url'])
            logging.info("({}/{} clicks) {} - {} - {} - {}".format(clicks_to_process-counter, total_clicks, result['status'], result['analysis_result'], result['http_message'], click['url']))
            if result['analysis_result'] == 'UNKNOWN' and result['status'] == 'NEW':
                # cloudphish is still working on this one
                continue
            elif result['analysis_result'] == 'ALERT':
                create_ace_alert(click)
                clicks.remove(click)
            else:
                logging.debug("removing {} from the queue".format(click['url']))
                analyzed_clicks.append(click)
            counter+=1

        #remove analyzed clicks
        clicks = [click for click in clicks if click not in analyzed_clicks]

        # if we're still waiting for cloudphish results for some clicks, give cloudphish 5 seconds
        if len(clicks) > 0:
            time.sleep(5)


if __name__ == '__main__':

    for path in [os.path.join(HOME_DIR, x) for x in ['log', 'var']]:
        if not os.path.isdir(path):
            try:
                os.mkdir(path)
            except Exception as e:
                raise Exception(str(e))

    # turn on logging
    logging_config_path = os.path.join(HOME_DIR, 'etc', 'logging.ini')
    logging.config.fileConfig(logging_config_path)
    #logging.getLogger('urllib3.connectionpool').setLevel(logging.ERROR)
    #logging.getLogger()
    #logger.setLevel(logging.DEBUG)


    logging.info("STARTING job at '{}'".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

    # load the config
    config_path = os.path.join(HOME_DIR, 'etc', 'config.ini')
    CONFIG = ConfigParser()
    CONFIG.read(config_path)

    search_name = CONFIG.get('url_click', 'splunk_search')
    search_path = os.path.join(HOME_DIR, 'lib', search_name)

    click_results = search_splunk(config_path, search_path)

    # ignore the proxy
    if 'https_proxy' in os.environ:
        del os.environ['https_proxy']

    check_cloudphish(click_results)

    logging.info("Job COMPLETED at '{}'".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

