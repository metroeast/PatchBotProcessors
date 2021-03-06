#!/usr/bin/env python3
#
# JPCImporter v2.1.1
#
# Tony Williams 2019-07-03
# David Elkin-Bram 2020-09-24
#
# ARW 2019-07-18 Many bug fixes
# ARW 2020-03-12 Version 2 with changes for new workflow
# ARW 2020-06-09 Some changes to log levels and cleaning up code
# ARW 2020-06-24 Final tidy before publication
# MVP-2 2020-09-24 Adjustments for recipe chaining and messaging

"""See docstring for JPCImporter class"""

from os import path
from shutil import copy2
import subprocess
import plistlib
import xml.etree.ElementTree as ET
import datetime
import logging
import logging.handlers
from time import sleep
import requests

from autopkglib import Processor, ProcessorError

APPNAME = "JPCImporter"
LOGLEVEL = logging.DEBUG
LOGFILE = "/usr/local/var/log/%s.log" % APPNAME

__all__ = [APPNAME]


class JPCImporter(Processor):
    """Uploads a package to JPC and updates the test install policy"""

    description = __doc__

    input_variables = {
        "pkg_path": {
            "required": False,
            "description": "Path to the package to be imported into Jamf Pro ",
        },
    }
    output_variables = {
        "pkg_path": {"description": "The created package.",},
        "jpc_importer_summary_result": {"description": "Summary of action"},
    }

    def setup_logging(self):
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


    def autopkg_msg(self, the_msg):
        """Defines a simple prefixed string to stdout for autopkg"""
        print(APPNAME + ': ' + the_msg)


    def load_prefs(self):
        """ load the preferences form file """
        # Which pref format to use, autopkg or jss_importer
        autopkg = False
        if autopkg:
            plist = path.expanduser(
                "~/Library/Preferences/com.github.autopkg.plist"
            )
            prefs = plistlib.load(open(plist, "rb"))
            url = prefs["JSS_URL"]
            auth = (prefs["API_USERNAME"], prefs["API_PASSWORD"])
        else:
            plist = path.expanduser("~/Library/Preferences/JPCImporter.plist")
            prefs = plistlib.load(open(plist, "rb"))
            url = prefs["url"]
            auth = (prefs["user"], prefs["password"])
        return (url, auth)

    def upload(self, pkg_path):
        """Upload the package `pkg_path` and returns the ID returned by JPC"""
        self.logger.info("Starting %s", pkg_path)

        # do some set up
        (server, auth) = self.load_prefs()
        hdrs = {"Accept": "application/xml", "Content-type": "application/xml"}
        base = server + "/JSSResource/"
        pkg = path.basename(pkg_path)
        title = pkg.split("-")[0]
        self.autopkg_msg("Starting JPC upload for pkg: %s" % pkg)

        # check to see if the package already exists
        url = base + "packages/name/{}".format(pkg)
        self.logger.debug("About to get: %s", url)
        ret = requests.get(url, auth=auth)
        if ret.status_code == 200:
            self.logger.warning("Found existing package: %s", pkg)
            self.autopkg_msg("Package filename already present in JPC, exiting")
            return 0

        # use curl for the file upload as it seems to work nicer than requests
        # for this ridiculous workaround for file uploads.
        curl_auth = "%s:%s" % auth
        curl_url = server + "/dbfileupload"
        command = ["curl", "-u", curl_auth, "-s", "-X", "POST", curl_url]
        command += ["--header", "DESTINATION: 0"]
        command += ["--header", "OBJECT_ID: -1"]
        command += ["--header", "FILE_TYPE: 0"]
        command += ["--header", "FILE_NAME: {}".format(pkg)]
        command += ["--upload-file", pkg_path]
        self.logger.debug("About to curl: %s", pkg)
        # self.logger.debug("Auth: %s", curl_auth)
        self.logger.debug("pkg_path: %s", pkg_path)
        self.logger.debug("command: %s", command)
        ret = subprocess.check_output(command)
        self.logger.debug("Done - ret: %s", ret)
        packid = ET.fromstring(ret).findtext("id")
        if packid == "":
            raise ProcessorError("curl failed for url :{}".format(curl_url))
        self.logger.debug("Uploaded and got ID: %s", packid)
        self.autopkg_msg("Upload complete, returned pkg ID: %s" % packid)

        # build the package record XML
        today = datetime.datetime.now().strftime("(%Y-%m-%d)")
        data = "<package><id>{}</id>".format(packid)
        data += "<category>Applications</category>"
        data += "<notes>Built by Autopkg. {}</notes></package>".format(today)

        # we use requests for all the other API calls as it codes nicer
        # update the package details
        url = base + "packages/id/{}".format(packid)
        # we set up some retries as sometimes the server
        # takes a minute to settle with a new package upload
        # (Can we have an API that allows for an upload and
        # setting this all in one go.)
        count = 0
        while True:
            count += 1
            self.logger.debug("package update attempt %s", count)
            ret = requests.put(url, auth=auth, headers=hdrs, data=data)
            if ret.status_code == 201:
                break
            self.logger.debug("Attempt failed with code: %s" % ret.status_code)
            self.logger.debug("URL: %s" % url)
            if count > 10:
                raise ProcessorError(
                    "Package update failed with code: %s" % ret.status_code
                )
            sleep(20)

        # now for the test policy update
        policy_name = "TEST-{}".format(title)
        self.autopkg_msg("Updating Policy: %s" % policy_name)
        url = base + "policies/name/{}".format(policy_name)
        ret = requests.get(url, auth=auth)
        if ret.status_code != 200:
            # object creation target
            # exit gracefully to allow chained processors to continue
            self.logger.debug(
                "Test Policy %s not found: %s" % (url, ret.status_code)
            )
            self.autopkg_msg("Policy not found, exiting")
            return 0
        self.logger.warning("Test policy found")
        root = ET.fromstring(ret.text)
        self.logger.debug("about to set package details")
        root.find("package_configuration/packages/package/id").text = str(
            packid
        )
        root.find("general/enabled").text = "false"
        root.find("package_configuration/packages/package/name").text = pkg
        url = base + "policies/id/{}".format(root.findtext("general/id"))
        data = ET.tostring(root)
        ret = requests.put(url, auth=auth, data=data)
        if ret.status_code != 201:
            raise ProcessorError(
                "Test policy %s update failed: %s" % (url, ret.status_code)
            )
        pol_id = ET.fromstring(ret.text).findtext("id")
        self.logger.debug("got pol_id: %s", pol_id)
        self.logger.info("Done Package: %s Test Policy: %s", pkg, pol_id)
        self.autopkg_msg("%s policy updated with pkg %s" % (policy_name, pkg))
        return pol_id

    def copy_local(self, pkg_path):
        """Copy the package `pkg_path` to a local path if specified in config"""
        self.logger.info("Copy to local path?")

        # load path from config
        plist = path.expanduser("~/Library/Preferences/JPCImporter.plist")
        prefs = plistlib.load(open(plist, "rb"))
        try:
            local_path = prefs["local_path"]
            
            # the package path is a sub directory inside the distribution point
            local_path += "Packages/"
            self.logger.info("config local path: %s", local_path)
            
            if not path.exists(local_path):
                self.logger.info("local path is not present, skipping copy")
                return
            
        except KeyError:
            # no path in config
            self.logger.info("no local_path configured, skipping")
            return
        
        # is the file already in the destination path?
        pkg = path.basename(pkg_path)
        if path.exists(local_path + pkg):
            self.logger.info("The pkg: %s is already at destination: %s", pkg, local_path)
            return
        
        # all is well, let's copy the file
        self.logger.info("Copying pkg from: %s :to: %s", pkg_path, local_path)
        self.autopkg_msg("Local copy from: %s :to: %s" % (pkg_path, local_path))
        try:
            copy_result = copy2(pkg_path, local_path)
            self.logger.info("File copy created: %s", copy_result)
        except IOError as err:
            self.logger.info("File copy IO Error: %s", err)
        
        # end copy_local
        return

    def main(self):
        """Do it!"""
        self.setup_logging()
        
        # clear any pre-existing summary result
        if "jpc_importer_summary_result" in self.env:
            del self.env["jpc_importer_summary_result"]
        pkg_path = self.env.get("pkg_path")
        if not path.exists(pkg_path):
            raise ProcessorError("Package not found: %s" % pkg_path)
        pol_id = self.upload(pkg_path)
        self.logger.debug("Done: %s: %s", pol_id, pkg_path)
        if pol_id != 0:
            # success, copy local file if config indicates
            self.copy_local(pkg_path)
            
            # report back to AutoPkg
            self.env["jpc_importer_summary_result"] = {
                "summary_text": "The following packages were uploaded:",
                "report_fields": ["policy_id", "pkg_path"],
                "data": {"policy_id": pol_id, "pkg_path": pkg_path},
            }


if __name__ == "__main__":
    PROCESSOR = JPCImporter()
    PROCESSOR.execute_shell()
