import json
import logging

import requests

import so_tag_mapping
from text_preprocess import preprocess_text

logging.basicConfig(format='%(asctime)s : %(levelname)s : %(message)s', level=logging.INFO)

# TODO: http://dcp2.jboss.org/v2/rest/search?size=50&field=_source&tag=rhel&type=stackoverflow_question


class StackOverflowDownloader:
    url = 'http://dcp2.jboss.org/v2/rest/search'
    project_name = None
    stemming = True
    preprocessor = None

    sep = ","

    gathered_attributes = ["sys_title", "sys_description", "sys_content_plaintext", "source"]

    def __init__(self, project, csv_sep=",", drop_stemming=True, preprocessor=(lambda x: x)):
        self.project_name = project
        self.sep = csv_sep
        self.stemming = drop_stemming
        self.preprocessor = preprocessor

    def json_to_csv(self, json_obj):
        out_csv = ""

        for entry in iter(json_obj):
            e_source = entry["_source"]
            line = ""
            for att in self.gathered_attributes:
                if att in e_source.keys():
                    # do not preprocess source identification
                    if att == "source":
                        line += '"%s"%s' % (self.preprocessor(e_source[att]), self.sep)
                    else:
                        processed_text = self.preprocessor(e_source[att])
                        line += '"%s"%s' % (processed_text, self.sep)
                else:
                    line += '""%s' % self.sep
            # print empty description tag for compatibility with std parser
            # print target category for category training / evaluation
            line += '"%s"\n' % self.project_name
            yield line

    def download_and_parse(self, response_size=50, sample=None):
        # retrieve product tags from mapping as retrieved from:
        # https://developers.redhat.com/sites/default/files/js/js_fP8gNSfNygBRHdDsIOIxFrpv92iS6fyy9Gogv03CC-U.js
        try:
            product_tags = so_tag_mapping.project_tag_mapping[self.project_name]["stackoverflow"]
            if product_tags is None:
                product_tags = []
        except KeyError:
            product_tags = []

        logging.info("Found %s tags for project %s" % (product_tags, self.project_name))

        download_counter = 0
        offset = 0
        increase = response_size

        if sample is None:
            sample = 100000

        for current_tag in product_tags:
            while increase >= response_size and offset < sample:
                params = {
                    'size': response_size,
                    'field': '_source',
                    'from': offset,
                    'type': 'stackoverflow_question',
                    'tag': current_tag
                }

                resp = requests.get(url=self.url, params=params)
                new_data = json.loads(resp.text)
                logging.info("Constructed url: %s" % resp.url)
                logging.info("Downloaded %s/%s content of '%s' tag (pulse %s)" % (download_counter,
                                                                                 new_data["hits"]["total"],
                                                                                 current_tag,
                                                                                 response_size))

                for line in self.json_to_csv(new_data["hits"]["hits"]):
                    yield line

                increase = len(new_data["hits"]["hits"])
                download_counter += increase
                offset += increase

        logging.info("Downloaded %s content items" % download_counter)
