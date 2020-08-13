#!/usr/bin/env python3
#
# JPCImporter v2.1
#
# Tony Williams 2019-07-03
#
# ARW 2019-07-18 Many bug fixes
# ARW 2020-03-12 Version 2 with changes for new workflow
# ARW 2020-06-09 Some changes to log levels and cleaning up code
# ARW 2020-06-24 Final tidy before publication
# ARW 2020-08-11 Code clean and refactor - now lints with a couple of disables

"""See docstring for JPCImporter class"""

# pylint: disable=invalid-name
# pylint: disable=too-few-public-methods

from os import path
import subprocess
import plistlib
import xml.etree.ElementTree as ET
import datetime
import logging
import logging.handlers
from time import sleep
import requests

from autopkglib import Processor, ProcessorError # pylint: disable=import-error

APPNAME = "JPCImporter"
LOGLEVEL = logging.DEBUG
LOGFILE = "/usr/local/var/log/%s.log" % APPNAME

__all__ = [APPNAME]


class Package:
    """A package. This exists merely to carry the variables"""

    title = ""  # the application title matching the test policy
    patch = ""  # name of the patch definition
    name = ""  # full name of the package '<title>-<version>.pkg'
    version = ""  # the version of our package
    idn = ""  # id of the package in our JP server


class JPCImporter(Processor):
    """Uploads a package to JPC and updates the test install policy"""

    description = __doc__

    input_variables = {
        "pkg_path": {
            "required": True,
            "description": "Path to the package to be imported into Jamf Pro ",
        },
    }
    output_variables = {
        "jpc_importer_summary_result": {"description": "Summary of action"}
    }

    def __init__(self):
        """Defines a nicely formatted logger"""

        self.logger = logging.getLogger(APPNAME)
        self.logger.setLevel(LOGLEVEL)
        # we may be the second and subsequent iterations of JPCImporter
        # and already have a handler.
        if len(self.logger.handlers) > 0:
            return
        handler = logging.handlers.TimedRotatingFileHandler(
            LOGFILE, when="D", interval=1, backupCount=7
        )
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        self.logger.addHandler(handler)

        # Which pref format to use, autopkg or jss_importer
        autopkg = False
        if autopkg:
            plist = path.expanduser(
                "~/Library/Preferences/com.github.autopkg.plist"
            )
            prefs = plistlib.load(open(plist, "rb"))
            self.url = prefs["JSS_URL"]
            self.auth = (prefs["API_USERNAME"], prefs["API_PASSWORD"])
        else:
            plist = path.expanduser("~/Library/Preferences/JPCImporter.plist")
            prefs = plistlib.load(open(plist, "rb"))
            self.server = prefs["url"]
            self.auth = (prefs["user"], prefs["password"])
         # do some set up
        self.hdrs = {"Accept": "application/xml", "Content-type": "application/xml"}
        self.base = self.server + "/JSSResource/"
        self.pkg = Package()
        self.pkg.pkg_path = self.env.get("pkg_path")
        self.pkg.name = path.basename(self.pkg.pkg_path)
        self.pkg.title = self.pkg.name.split("-")[0]

    def upload(self, pkg_path):
        """Upload the package `pkg_path` and returns the ID returned by JPC"""
        self.logger.info("Starting %s", pkg_path)

        # check to see if the package already exists
        url = self.base + "packages/name/{}".format(self.pkg.name)
        self.logger.debug("About to get: %s", url)
        ret = requests.get(url, auth=self.auth)
        if ret.status_code == 200:
            self.logger.warning("Found existing package: %s", self.pkg.name)
            return 0

        # use curl for the file upload as it seems to work nicer than requests
        command = ["curl", "-u", "%s:%s" % self.auth, "-s"]
        command += ["-X", "POST", self.server + "/dbfileupload"]
        command += ["--header", "DESTINATION: 0", "--header", "OBJECT_ID: -1"]
        command += ["--header", "FILE_TYPE: 0"]
        command += ["--header", f"FILE_NAME: {self.pkg.name}"]
        command += ["--upload-file", pkg_path]
        self.logger.debug("About to curl: %s", self.pkg.name)
        # self.logger.debug("Auth: %s", curl_auth)
        ret = subprocess.check_output(command)
        self.logger.debug("Done - ret: %s", ret)
        packid = ET.fromstring(ret).findtext("id")
        if packid == "":
            raise ProcessorError(
                f"curl failed for url :{self.server}/dbfileupload"
            )
        self.logger.debug("Uploaded and got ID: %s", packid)

        # build the package record XML
        data = f"<package><id>{packid}</id>"
        data += "<category>Applications</category>"
        data += "<notes>Built by Autopkg. {}</notes></package>".format(
            datetime.datetime.now().strftime("(%Y-%m-%d)")
        )

        # we use requests for all the other API calls as it codes nicer
        # update the package details
        url = f"{self.base}packages/id/{packid}"
        # we set up some retries as sometimes the server
        # takes a minute to settle with a new package upload
        count = 0
        while True:
            count += 1
            self.logger.debug("package update attempt %s", count)
            ret = requests.put(url, auth=self.auth, headers=self.hdrs, data=data)
            if ret.status_code == 201:
                break
            self.logger.debug("Attempt failed with code: %s URL: %s", ret.status_code, url)
            if count > 10:
                raise ProcessorError(
                    f"Package update failed with code: {ret.status_code}"
                )
            sleep(20)

        # now for the test policy update
        url = f"{self.base}policies/name/TEST-{self.pkg.title}"
        ret = requests.get(url, auth=self.auth)
        if ret.status_code != 200:
            raise ProcessorError(
                f"Test Policy {url} not found: {ret.status_code}"
            )
        self.logger.warning("Test policy found")
        root = ET.fromstring(ret.text)
        root.find("package_configuration/packages/package/id").text = str(
            packid
        )
        root.find("general/enabled").text = "false"
        root.find("package_configuration/packages/package/name").text = self.pkg.name
        url = f"{self.base}policies/id/{root.findtext('general/id')}"
        data = ET.tostring(root)
        ret = requests.put(url, auth=self.auth, data=data)
        if ret.status_code != 201:
            raise ProcessorError(
                f"Test policy {url} update failed: {ret.status_code}"
            )
        pol_id = ET.fromstring(ret.text).findtext("id")
        self.logger.info("Done Package: %s Test Policy: %s", self.pkg.name, pol_id)
        return pol_id

    def main(self):
        """Do it!"""
        # clear any pre-existing summary result
        if "jpc_importer_summary_result" in self.env:
            del self.env["jpc_importer_summary_result"]
        pkg_path = self.env.get("pkg_path")
        if not path.exists(self.pkg_path):
            raise ProcessorError(f"Package not found: {pkg_path}")
        pol_id = self.upload(self.pkg_path)
        self.logger.debug("Done: %s: %s", pol_id, self.pkg_path)
        if pol_id != 0:
            self.env["jpc_importer_summary_result"] = {
                "summary_text": "The following packages were uploaded:",
                "report_fields": ["policy_id", "pkg_path"],
                "data": {"policy_id": pol_id, "pkg_path": pkg_path},
            }


if __name__ == "__main__":
    PROCESSOR = JPCImporter()
    PROCESSOR.execute_shell()
