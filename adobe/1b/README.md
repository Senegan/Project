# Challenge 1b: Multi-Collection PDF Analysis

## Overview
This project analyzes multiple PDF document collections and extracts the most relevant content based on a specified persona and job-to-be-done. It is designed for the "Persona-Driven Document Intelligence" challenge.

## Features
- Persona-based content analysis
- Importance ranking of extracted sections
- Multi-collection document processing
- Structured JSON output with metadata

## Project Structure
```
Collection_X/
├── PDFs/                  # PDF files for the collection
├── challenge1b_input.json # Input configuration
└── challenge1b_output.json# Analysis results
```

## Requirements
- Python 3.10+
- Docker 

## Setup
Install dependencies:
```sh
pip install -r requirements.txt
```

## Usage
To run the script directly:
```sh
python pdf_extractor_1b.py
```

To build and run with Docker:
```sh
docker build -t pdf-analyzer .
docker run --rm -v $(pwd):/app pdf-analyzer
```

## Input/Output
- Place your PDFs and `challenge1b_input.json` in the collection folder.
- The script will generate `challenge1b_output.json` with the analysis results.

## Notes
- The script processes all PDFs listed in the input JSON.
- Output format matches the challenge requirements.
