# PDF Outline Extractor (Dockerized)

This project extracts a structured outline (Title, H1, H2, H3) from all PDF files in a directory, outputting a JSON file for each PDF. It is designed for hackathon/competition use and runs fully offline in a Docker container.

## Features
- Batch processes all PDFs in a directory
- Outputs a JSON file for each PDF with title and outline
- Heuristic heading detection (font size, boldness, position)
- No duplicate or symbol-only headings
- Ignores page markers like "Page 3 of 4"
- Dockerized for easy, reproducible execution

## Requirements
- Docker (Linux/Windows/Mac, AMD64/x86_64 compatible)

## Directory Structure
```
project-root/
├── pdf.py
├── requirements.txt
├── Dockerfile
├── .dockerignore
├── README.md
├── input/           # Place your PDF files here
└── output/          # JSON output will be written here
```

## How to Build the Docker Image
From the project root directory, run:

```
docker build -t pdf-outline-extractor .
```

## How to Run the Container
Assuming your PDFs are in `input/` and you want JSONs in `output/`:

```
docker run --rm \
  -v ${PWD}/input:/app/input \
  -v ${PWD}/output:/app/output \
  pdf-outline-extractor
```

- On Windows PowerShell, use:
  ```
  docker run --rm -v ${PWD}/input:/app/input -v ${PWD}/output:/app/output pdf-outline-extractor
  ```
- On Windows CMD, use:
  ```
  docker run --rm -v %cd%\input:/app/input -v %cd%\output:/app/output pdf-outline-extractor
  ```

## What Happens
- All PDFs in `/app/input` are processed
- For each `filename.pdf`, a `filename.json` is created in `/app/output`

## Notes
- No internet/network access is required or used
- Only CPU is used (no GPU dependencies)
- Works on AMD64/x86_64 (Intel/AMD CPUs)
- All dependencies are installed via `requirements.txt`

## Troubleshooting
- If you don't see output files, check your volume mount paths and permissions
- Make sure Docker Desktop is running (on Windows/Mac)
- Check the container logs for errors

