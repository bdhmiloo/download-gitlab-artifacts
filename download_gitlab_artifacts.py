import requests
import argparse
import os
import zipfile
import json
import shutil
import urllib3
import html
from reportlab.lib.pagesizes import A4
from reportlab.platypus import XPreformatted, SimpleDocTemplate, Paragraph
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from concurrent.futures import ThreadPoolExecutor
import xml.etree.ElementTree as ET

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class PipelineArtifactFetcher:
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip('/')
        self.headers = {'PRIVATE-TOKEN': token}

    def get_job_id(self, project_id, pipeline_id, job_name):
        """Gets the job ID for a given job name in a specific pipeline, iterating over paginated results"""
        page = 1
        while True:
            url = f"{self.base_url}/projects/{project_id}/pipelines/{pipeline_id}/jobs?per_page=100&page={page}"
            response = requests.get(url, headers=self.headers, verify=False)
            response.raise_for_status()
            jobs = response.json()
            for job in jobs:
                if job['name'] == job_name:
                    return job['id']
            next_page = response.headers.get('X-Next-Page')
            if not next_page:
                break
            page = int(next_page)
        raise ValueError(f"Job '{job_name}' not found")

    def download_artifact(self, project_id, job_id):
        """Downloads artifacts for a specific job ID and returns the path to the downloaded zip file"""
        url = f"{self.base_url}/projects/{project_id}/jobs/{job_id}/artifacts"
        response = requests.get(url, headers=self.headers, verify=False, stream=True)
        response.raise_for_status()
        output_filename = f"job_{job_id}_artifacts.zip"
        with open(output_filename, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return output_filename

def clean_output_dir(output_dir):
    """Removes all contents of output_dir, if it exists"""
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)

def unzip_to_dir(zip_path, extract_dir):
    """Unzips artifacts to directory (no renaming)"""
    if not os.path.exists(extract_dir):
        os.makedirs(extract_dir)
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_dir)
    os.remove(zip_path)
    return extract_dir

def json_to_pdf(json_path, pdf_path):
    """Converts JSON file to PDF, preserving structure and indentation"""
    with open(json_path, 'r') as f:
        json_data = json.load(f)
    json_str = json.dumps(json_data, indent=2)
    doc = SimpleDocTemplate(pdf_path, pagesize=A4)
    styles = getSampleStyleSheet()
    monospace = ParagraphStyle(
        name='Monospace',
        parent=styles['Normal'],
        fontName='Courier',
        fontSize=10,
        alignment=TA_LEFT,
        leading=14,
        spaceBefore=6,
        spaceAfter=6
    )
    elements = []
    title = Paragraph(os.path.basename(json_path), styles['Title'])
    elements.append(title)

    for line in json_str.split('\n'):
        # To preserve indentation: use non-breaking spaces for leading spaces (optional)
        # This is not strictly necessary for most monospace fonts, but ensures perfect alignment
        leading_spaces = len(line) - len(line.lstrip(' '))
        if leading_spaces > 0:
            line = '&nbsp;' * leading_spaces + line[leading_spaces:]
        elements.append(Paragraph(line, monospace))
    doc.build(elements)

def xml_to_pdf(xml_path, pdf_path):
    """Converts XML file to PDF, preserving structure, indentation, and wrapping lines"""
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        xml_str = ET.tostring(root, encoding='unicode', method='xml')

        doc = SimpleDocTemplate(pdf_path, pagesize=A4)
        styles = getSampleStyleSheet()
        monospace = ParagraphStyle(
            name='Monospace',
            parent=styles['Normal'],
            fontName='Courier',
            fontSize=10,
            alignment=TA_LEFT,
            leading=14,
            spaceBefore=6,
            spaceAfter=6,
            wordWrap='LTR'  # Ensures lines are wrapped properly
        )
        elements = []
        title = Paragraph(html.escape(os.path.basename(xml_path)), styles['Title'])
        elements.append(title)

        for line in xml_str.split('\n'):
            escaped_line = html.escape(line)
            elements.append(Paragraph(escaped_line, monospace))
        doc.build(elements)
    except ET.ParseError as e:
        print(f"❌ XML parsing error: {str(e)}")
    except Exception as e:
        print(f"❌ Failed to convert XML to PDF: {str(e)}")

def zip_pdfs(output_dir, zip_filename="reports.zip", pdf_prefix=""):
    """Zips all PDF files in output_dir and its subdirectories, placing them at the root of the ZIP"""
    pdf_files = []
    for root, _, files in os.walk(output_dir):
        for file in files:
            if file.lower().endswith('.pdf'):
                pdf_files.append(os.path.join(root, file))
    if not pdf_files:
        print("⚠️ No PDF files found to zip.\n")
        return
    zip_path = os.path.join(output_dir, f"{pdf_prefix}{zip_filename}")
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for pdf_file in pdf_files:
            arcname = f"{pdf_prefix}{os.path.basename(pdf_file)}"
            zipf.write(pdf_file, arcname)
    print(f"✅ Zipped all PDFs to {zip_path}\n")

def process_job(job_name, base_url, token, project_id, pipeline_id, output_dir):
    """Processes a single job: fetches artifacts, unzips them, converts JSON to PDF"""
    try:
        fetcher = PipelineArtifactFetcher(base_url, token)
        job_id = fetcher.get_job_id(project_id, pipeline_id, job_name)
        zip_path = fetcher.download_artifact(project_id, job_id)
        print(f"📦 Downloaded artifacts for {job_name} to {zip_path}")

        extract_dir = os.path.join(output_dir, f"job_{job_id}")
        unzip_to_dir(zip_path, extract_dir)
        for filename in os.listdir(extract_dir):
            if filename.endswith('.json'):
                json_path = os.path.join(extract_dir, filename)
                pdf_path = os.path.splitext(json_path)[0] + '.pdf'
                json_to_pdf(json_path, pdf_path)
            elif filename.endswith('.xml'):
                xml_path = os.path.join(extract_dir, filename)
                pdf_path = os.path.splitext(xml_path)[0] + '.pdf'
                xml_to_pdf(xml_path, pdf_path)
        return True
    except Exception as e:
        print(f"❌ Failed to process {job_name}: {str(e)}")
        return False

def main():
    """Main function to parse arguments and process artifacts"""
    parser = argparse.ArgumentParser(description='Fetch and process CI/CD artifacts')
    parser.add_argument('--output-dir', default='artifacts', help='Output directory')
    parser.add_argument('--config', required=True, help='Path to config file (JSON)')
    args = parser.parse_args()

    token = os.getenv('GITLAB_TOKEN')
    if not token:
        print("Error: GITLAB_TOKEN environment variable not set.")
        exit(1)

    base_url = os.getenv('GITLAB_BASE_URL')
    if not base_url:
        print("Error: GITLAB_BASE_URL environment variable not set.")
        exit(1)

    if not os.path.exists(args.config):
        print(f"Error: File '{args.config}' not found.")
        exit(1)

    try:
        with open(args.config, 'r') as f:
            configs = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON in config file: {e}")
        exit(1)
    except Exception as e:
        print(f"Error reading config file: {e}")
        exit(1)

    for config in configs:
        project_id = config['project_id']
        pipeline_id = config['pipeline_id']
        pdf_prefix = config['pdf_prefix']
        job_names = config['job_names']
        output_dir = os.path.join(args.output_dir, f"project_{project_id}_pipeline_{pipeline_id}")
        clean_output_dir(output_dir)
        job_args = [(job_name, base_url, token, project_id, pipeline_id, output_dir)
                    for job_name in job_names]
        with ThreadPoolExecutor() as executor:
            futures = [executor.submit(process_job, *args) for args in job_args]
            for future in futures:
                future.result()
        zip_pdfs(output_dir, pdf_prefix=pdf_prefix)

if __name__ == '__main__':
    main()
