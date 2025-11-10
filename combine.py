import logging
import os
from pathlib import Path
from pprint import pformat
import ssl
import sys
import ftplib
import zipfile
import re
import requests
import shutil
import time

# 📢 MODIFY VALUES HERE AS NECESSARY
HOME_ASSISTANT_URL = "http://homeassistant.local:8123"

FILAMENT_NUMBER_REGEX = r"; filament: ([\d,]+)"
FILAMENT_NUMBER_SEP = ","

FILAMENT_COST_VAR = "input_number.3d_current_cost"
FILAMENT_COST_REGEX = r"; filament_cost = ([\d\.,]+)"
FILAMENT_COST_SEP = ","

FILAMENT_WEIGHT_VAR = "input_number.3d_current_weight"
FILAMENT_WEIGHT_REGEX = r"; total filament weight \[g\] : ([\d\.,]+)"
FILAMENT_WEIGHT_SEP = ","

FILAMENT_LENGTH_VAR = "input_number.3d_current_length"
FILAMENT_LENGTH_REGEX = r"; total filament length \[mm\] : ([\d\.,]+)"
FILAMENT_LENGTH_SEP = ","

FILAMENT_TYPE_VAR = "input_text.3d_current_type"
FILAMENT_TYPE_REGEX = r"; filament_type = ([a-zA-Z;]+)"
FILAMENT_TYPE_SEP = ";"

# * Logger
file_handler = logging.FileHandler(Path(__file__).with_suffix(".log"))
stdout_handler = logging.StreamHandler(sys.stdout)
handlers = [file_handler, stdout_handler]
logging.basicConfig(
    level=logging.DEBUG,
    format="[%(asctime)s] {%(filename)s:%(lineno)d} %(levelname)s - %(message)s",
    handlers=handlers,
)
logger = logging.Logger(Path(__file__).stem)


def read_env(path=Path(__file__).parent / ".env"):
    pairlist = []
    with open(path, "r") as envfile:
        lines = filter(lambda l: l, envfile.readlines())
        pairlist = map(lambda l: list(map(lambda v: v.strip(), l.split("="))), lines)

    for key, value in pairlist:
        os.environ[key] = value.strip("'\"")


print(read_env())


class ImplicitFTP_TLS(ftplib.FTP_TLS):
    """
    FTP_TLS subclass that automatically wraps sockets in SSL to support implicit FTPS.
    see https://stackoverflow.com/a/36049814
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sock = None

    @property
    def sock(self):
        """Return the socket."""
        return self._sock

    @sock.setter
    def sock(self, value):
        """When modifying the socket, ensure that it is ssl wrapped."""
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value)
        self._sock = value


def download_model(host, access_code, model, download_path="."):
    download_path = os.path.abspath(download_path)
    os.makedirs(download_path, exist_ok=True)
    local_file_path = os.path.join(download_path, model)

    try:
        ftp = ImplicitFTP_TLS()
        ftp.connect(host=host, port=990, timeout=10)
        ftp.login(user="bblp", passwd=access_code)
        ftp.prot_p()

        # Get a list of available files to handle special characters
        available_files = ftp.nlst()
        logging.debug("Files on FTP Server:")
        logging.debug(pformat(available_files))  # Debugging line

        # Check if the exact filename exists
        if model not in available_files:
            logging.debug(
                f"File {model} not found in root. Checking cache/ directory..."
            )
            ftp.cwd("cache")
            available_files = ftp.nlst()
            if model not in available_files:
                logging.error(f"Error: File {model} not found on FTP server!")
                ftp.quit()
                return
        else:
            logging.error(f"File {model} found in root directory.")

        # Download file
        with open(local_file_path, "wb") as fp:
            ftp.retrbinary(f"RETR {model}", fp.write)

        ftp.quit()

        # Verify file size after download
        if os.path.getsize(local_file_path) == 0:
            logging.error(f"Error: Downloaded file is 0KB! Something went wrong.")
        else:
            logging.debug(
                f"Successfully downloaded {model} ({os.path.getsize(local_file_path)} bytes)"
            )

    except Exception as e:
        logging.error(f"FTP download failed: {e}")


def extract_image_and_gcode(model_path, extract_path="www/bblab"):
    """
    Extracts the image and the G-code file from a 3MF archive and saves them with fixed names.
    Deletes all other extracted files/folders before renaming and moving the important ones.
    """
    model_path = os.path.abspath(model_path)
    extract_path = os.path.abspath(extract_path)

    logging.debug(f"Checking file: {model_path}")

    time.sleep(2)  # Ensure the file is fully downloaded before processing

    if not os.path.exists(model_path):
        logger.error(f"Error: File {model_path} does not exist!")
        return

    if not zipfile.is_zipfile(model_path):
        logger.error(f"Error: {model_path} is not a valid 3MF (ZIP) file.")
        return

    try:
        with zipfile.ZipFile(model_path, "r") as archive:
            logging.debug("3MF archive successfully opened. Extracting all files...")
            archive.extractall(extract_path)  # Extract everything

            # Print extracted structure for debugging
            for root, dirs, files in os.walk(extract_path):
                logging.debug(f"Extracted folder: {root}")
                for file in files:
                    logging.debug(f"  - {file}")

            # Clean up unnecessary files
            gcode_file = None
            image_file = None
            all_files = []

            # Identify all extracted files and store them for deletion (except gcode and image)
            for root, dirs, files in os.walk(extract_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    all_files.append(file_path)

                    # Check for the G-code and image file
                    if file.lower().endswith(".gcode"):
                        gcode_file = file_path
                        possible_image_file = Path(gcode_file).with_suffix(".png")
                        if os.path.isfile(possible_image_file):
                            image_file = str(possible_image_file)
                    elif not image_file and file.lower() == "plate_1.png":
                        image_file = file_path

            # Now delete all files except the G-code and image files
            for file_path in all_files:
                if file_path not in [gcode_file, image_file]:
                    try:
                        os.remove(file_path)
                        logging.debug(f"Deleted extra file: {file_path}")
                    except Exception as e:
                        logger.error(f"Error deleting file {file_path}: {e}")

            # Delete empty directories (and non-empty if they don't contain the needed files)
            for root, dirs, files in os.walk(extract_path, topdown=False):
                for dir in dirs:
                    dir_path = os.path.join(root, dir)
                    # Only remove empty directories, if they don't have our files
                    if not os.listdir(dir_path):  # Empty directory
                        shutil.rmtree(dir_path)
                        logging.debug(f"Deleted empty folder: {dir_path}")

            # After cleanup, now rename and move the important files
            final_gcode_path = os.path.join(extract_path, "plate_1.gcode")
            if gcode_file:
                shutil.move(gcode_file, final_gcode_path)
                logging.debug(f"Extracted real G-code: {final_gcode_path}")
                extract_filament_data(final_gcode_path)
            else:
                logger.error("Error: No valid G-code file found after extraction.")

            final_image_path = os.path.join(extract_path, "cover_image.png")
            if image_file:
                shutil.move(image_file, final_image_path)
                logging.debug(f"Extracted image to {final_image_path}")
            else:
                logger.error("Error: plate_1.png not found in the 3MF archive.")

        # Remove the original .3mf file
        os.remove(model_path)
        logging.debug(f"Deleted the .3mf file: {model_path}")

    except Exception as e:
        logger.error(f"An error occurred: {e}")


def extract_filament_data(gcode_file_path):
    """
    Extract filament data (weight, cost, length, type) from a G-code file and
    print it out (or update Home Assistant helpers as needed).
    """
    filament_index_list = []
    filament_cost_list = []
    filament_weight_list = []
    filament_length_list = []
    filament_type = None

    with open(gcode_file_path, "r") as file:
        content = file.read()

        filament_number_match = re.search(FILAMENT_NUMBER_REGEX, content)
        if filament_number_match:
            filament_index_list = filament_number_match.group(1).split(
                FILAMENT_NUMBER_SEP
            )
            filament_index_list = list(map(lambda n: int(n) - 1, filament_index_list))

        cost_match = re.search(FILAMENT_COST_REGEX, content)
        if cost_match:
            all_filament_cost_list = cost_match.group(1).split(FILAMENT_COST_SEP)
            all_filament_cost_list = list(
                map(lambda n: float(n), all_filament_cost_list)
            )
            filament_cost_list = list(
                map(
                    lambda f_index: all_filament_cost_list[f_index], filament_index_list
                )
            )

        weight_match = re.search(FILAMENT_WEIGHT_REGEX, content)
        if weight_match:
            filament_weight_list = weight_match.group(1).split(FILAMENT_WEIGHT_SEP)
            filament_weight_list = list(map(lambda n: float(n), filament_weight_list))

        length_match = re.search(FILAMENT_LENGTH_REGEX, content)
        if length_match:
            filament_length_list = length_match.group(1).split(FILAMENT_LENGTH_SEP)
            filament_length_list = list(
                map(lambda n: float(n) / 1000, filament_length_list)
            )

        type_match = re.search(FILAMENT_TYPE_REGEX, content)
        if type_match:
            filament_type_list = type_match.group(1).split(FILAMENT_TYPE_SEP)
            filament_type = filament_type_list[
                filament_index_list[0]
            ]  # Always same type of filament thought the print

    logging.debug("filament_number_list: " + pformat(filament_index_list))
    logging.debug("filament_cost_list: " + pformat(filament_cost_list))
    logging.debug("filament_weight_list: " + pformat(filament_weight_list))
    logging.debug("filament_length_list: " + pformat(filament_length_list))

    filament_cost = sum(
        map(lambda e: e[0] / 1000 * e[1], zip(filament_weight_list, filament_cost_list))
    )
    filament_weight = sum(filament_weight_list)
    filament_length = sum(filament_length_list)

    if filament_cost:
        logging.debug(f"filament_cost: {filament_cost} currency")
        update_home_assistant_helper(FILAMENT_COST_VAR, filament_cost)
    else:
        logger.error(f"filament_cost not found")

    if filament_weight:
        logging.debug(f"filament_weight: {filament_weight} g")
        update_home_assistant_helper(FILAMENT_WEIGHT_VAR, filament_weight)
    else:
        logger.error(f"filament_weight not found")

    if filament_length:
        logging.debug(f"filament_length: {filament_length} m")
        update_home_assistant_helper(FILAMENT_LENGTH_VAR, filament_length)
    else:
        logger.error(f"filament_length not found")

    if filament_type:
        logging.debug(f"filament_type: {filament_type}")
        update_home_assistant_helper(FILAMENT_TYPE_VAR, filament_type)
    else:
        logger.error(f"filament_type not found")


def update_home_assistant_helper(helper_name, value):
    """
    Update Home Assistant input helper while preserving metadata.
    """
    url = f"{HOME_ASSISTANT_URL}/api/states/{helper_name}"
    headers = {
        "Authorization": "Bearer " + os.environ["BEARER_TOKEN"],
        "Content-Type": "application/json",
    }

    try:
        get_response = requests.get(
            url, headers={"Authorization": headers["Authorization"]}
        )
        get_response.raise_for_status()
        current_state = get_response.json()

        payload = {
            "state": str(value),
            "attributes": current_state.get("attributes", {}),
        }

        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        logging.debug(f"Successfully updated {helper_name} with value: {value}")

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to update {helper_name}: {e}")


def main():
    if len(sys.argv) < 4:
        logger.error("Host, access code, and model name required")
    else:
        printer_ip = sys.argv[1]
        access_code = sys.argv[2]
        model_name = sys.argv[3]

        download_model(
            host=printer_ip,
            access_code=access_code,
            model=model_name,
            download_path="www/bblab",
        )

        extract_image_and_gcode(f"www/bblab/{model_name}", extract_path="www/bblab")


if __name__ == "__main__":
    main()
