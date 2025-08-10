# Project
How to Run Journey Planner in Docker
🧱 Prerequisites
Docker Desktop installed and running

📦 Build the Docker Image
docker build -t journey-planner .


🚀 Run the Container
docker run -p 5000:5000 --name my-journey-container journey-planner

This maps the app to your local machine so you can access it in your browser at:
http://localhost:5000


🛑 Stop the Container
docker stop my-journey-container
docker rm my-journey-container