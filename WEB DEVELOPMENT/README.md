# Project
this only for project using docker folder
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

# the above other files are run in the normal localhost in our device 
inside Docker we can't fetch train data as IRCTC have BOT PRotection so to fetch train data we need to run this in our cmd or vs code

🚀 Run the project
python main.py

This maps the app to your local machine so you can access it in your browser at:
http://127.0.0.1:5000