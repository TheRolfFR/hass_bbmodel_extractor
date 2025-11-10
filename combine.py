import os
import ssl
import sys
import ftplib
import zipfile
import re
import requests
import urllib.parse
import shutil
import time

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

def download_model(host, access_code, model, download_path='.'):
    download_path = os.path.abspath(download_path)
    os.makedirs(download_path, exist_ok=True)
    local_file_path = os.path.join(download_path, model)

    try:
        ftp = ImplicitFTP_TLS()
        ftp.connect(host=host, port=990, timeout=10)
        ftp.login(user='bblp', passwd=access_code)
        ftp.prot_p()

        # Get a list of available files to handle special characters
        available_files = ftp.nlst()
        print("Files on FTP Server:", available_files)  # Debugging line

        # Check if the exact filename exists
        if model not in available_files:
            print(f"File {model} not found in root. Checking cache/ directory...")
            ftp.cwd('cache')
            available_files = ftp.nlst()
            if model not in available_files:
                print(f"Error: File {model} not found on FTP server!")
                ftp.quit()
                return
        else:
            print(f"File {model} found in root directory.")

        # Download file
        with open(local_file_path, 'wb') as fp:
            ftp.retrbinary(f'RETR {model}', fp.write)

        ftp.quit()

        # Verify file size after download
        if os.path.getsize(local_file_path) == 0:
            print(f"Error: Downloaded file is 0KB! Something went wrong.")
        else:
            print(f"Successfully downloaded {model} ({os.path.getsize(local_file_path)} bytes)")

    except Exception as e:
        print(f"FTP download failed: {e}")


def extract_image_and_gcode(model_path, extract_path="www/bblab"):
    """
    Extracts the image and the G-code file from a 3MF archive and saves them with fixed names.
    Deletes all other extracted files/folders before renaming and moving the important ones.
    """
    model_path = os.path.abspath(model_path)
    extract_path = os.path.abspath(extract_path)

    print(f"Checking file: {model_path}")

    time.sleep(2)  # Ensure the file is fully downloaded before processing

    if not os.path.exists(model_path):
        print(f"Error: File {model_path} does not exist!")
        return

    if not zipfile.is_zipfile(model_path):
        print(f"Error: {model_path} is not a valid 3MF (ZIP) file.")
        return

    try:
        with zipfile.ZipFile(model_path, 'r') as archive:
            print("3MF archive successfully opened. Extracting all files...")
            archive.extractall(extract_path)  # Extract everything

            # Print extracted structure for debugging
            for root, dirs, files in os.walk(extract_path):
                print(f"Extracted folder: {root}")
                for file in files:
                    print(f"  - {file}")

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
                    elif file.lower() == "plate_1.png":
                        image_file = file_path

            # Now delete all files except the G-code and image files
            for file_path in all_files:
                if file_path not in [gcode_file, image_file]:
                    try:
                        os.remove(file_path)
                        print(f"Deleted extra file: {file_path}")
                    except Exception as e:
                        print(f"Error deleting file {file_path}: {e}")

            # Delete empty directories (and non-empty if they don't contain the needed files)
            for root, dirs, files in os.walk(extract_path, topdown=False):
                for dir in dirs:
                    dir_path = os.path.join(root, dir)
                    # Only remove empty directories, if they don't have our files
                    if not os.listdir(dir_path):  # Empty directory
                        shutil.rmtree(dir_path)
                        print(f"Deleted empty folder: {dir_path}")

            # After cleanup, now rename and move the important files
            final_gcode_path = os.path.join(extract_path, 'plate_1.gcode')
            if gcode_file:
                shutil.move(gcode_file, final_gcode_path)
                print(f"Extracted real G-code: {final_gcode_path}")
                extract_filament_data(final_gcode_path)
            else:
                print("Error: No valid G-code file found after extraction.")

            final_image_path = os.path.join(extract_path, 'cover_image.png')
            if image_file:
                shutil.move(image_file, final_image_path)
                print(f"Extracted image to {final_image_path}")
            else:
                print("Error: plate_1.png not found in the 3MF archive.")

        # Remove the original .3mf file
        os.remove(model_path)
        print(f"Deleted the .3mf file: {model_path}")

    except Exception as e:
        print(f"An error occurred: {e}")
def extract_filament_data(gcode_file_path):
    """
    Extract filament data (weight, cost, length, type) from a G-code file and 
    print it out (or update Home Assistant helpers as needed).
    """
    filament_cost = None
    filament_weight = None
    filament_length = None
    filament_type = None

    with open(gcode_file_path, 'r') as file:
        content = file.read()
        
        cost_match = re.search(r'; filament cost = ([\d\.]+)', content)
        if cost_match:
            filament_cost = cost_match.group(1)
        
        weight_match = re.search(r'; filament used \[g\] = ([\d\.]+)', content)
        if weight_match:
            filament_weight = weight_match.group(1)

        length_match = re.search(r'; filament used \[mm\] = ([\d\.]+)', content)
        if length_match:
            filament_length = length_match.group(1)

        type_match = re.search(r'; filament_type = (.+)', content)
        if type_match:
            filament_type = type_match.group(1)


    if filament_cost:
        print(f"{filament_cost}")
        update_home_assistant_helper("input_number.3d_current_cost", filament_cost)

    if filament_weight:
        print(f"{filament_weight}")
        update_home_assistant_helper("input_number.3d_current_weight", filament_weight)

    if filament_length:
        print(f"{filament_length}")
        update_home_assistant_helper("input_number.3d_current_length", filament_length)

    if filament_type:
        print(f"{filament_type}")
        update_home_assistant_helper("input_text.3d_current_type", filament_type)

def update_home_assistant_helper(helper_name, value):
    """
    Update Home Assistant input helper while preserving metadata.
    """
    url = f'http://homeassistant.local:8123/api/states/{helper_name}'
    headers = {
        'Authorization': 'Bearer <PLACE_TOKEN_HERE>',
        'Content-Type': 'application/json',
    }
    
    try:
        get_response = requests.get(url, headers={'Authorization': headers['Authorization']})
        get_response.raise_for_status()
        current_state = get_response.json()
        

        payload = {
            'state': str(value),
            'attributes': current_state.get('attributes', {})
        }
        

        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        print(f"Successfully updated {helper_name} with value: {value}")
    
    except requests.exceptions.RequestException as e:
        print(f"Failed to update {helper_name}: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Host, access code, and model name required")
    else:
        printer_ip = sys.argv[1]
        access_code = sys.argv[2]
        model_name = sys.argv[3]


        download_model(host=printer_ip, access_code=access_code, model=model_name, download_path='www/bblab')


        extract_image_and_gcode(f'www/bblab/{model_name}', extract_path='www/bblab')
