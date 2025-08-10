from flask import Flask, request, render_template_string, redirect, url_for, flash, session
from utils import get_coordinates, get_city_from_coords, find_best_bus_stand, extract_city, find_nearby_transport
from mtc import load_mtc_routes, get_bus_fares, build_route_steps, generate_route_details, calculate_total_fare
from tn import get_tnstc_bus_schedules
from redbus import get_redbus_schedules
from abhibus import get_abhibus_schedules, get_abhibus_city_id
from geopy.distance import geodesic
from auth import init_db, register_user, login_user, get_user_history, get_user_profile
import logging
import datetime
import os
from datetime import datetime
import sys
import io
import urllib3
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Fix Unicode output for Windows
if sys.stdout.encoding != 'UTF-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
if sys.stderr.encoding != 'UTF-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("transport_finder.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)

app = Flask(__name__)
app.secret_key = 'your_very_secure_secret_key_12345'  # Change this in production

# Initialize database
init_db()

# Base HTML template with navigation
BASE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Journey Planner TN</title>
    <!-- Bootstrap CSS -->
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <!-- Font Awesome -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.2/css/all.min.css">
    <style>
        .navbar-brand { font-weight: 700; }
        .nav-link { transition: all 0.3s; }
        .nav-link:hover { transform: translateY(-2px); }
        .form-container { max-width: 800px; }
        .history-item { border-bottom: 1px solid #eee; padding: 15px 0; }
        .history-item:last-child { border-bottom: none; }
        .card-header-bg { background: linear-gradient(to right, #f59e0b, #d97706); }
    </style>
</head>
<body>
    <!-- Navigation Bar -->
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark">
        <div class="container">
            <a class="navbar-brand" href="/">
                <i class="fas fa-compass me-2"></i>Journey Planner TN
            </a>
            <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
                <span class="navbar-toggler-icon"></span>
            </button>
            <div class="collapse navbar-collapse" id="navbarNav">
                <ul class="navbar-nav me-auto">
                    <li class="nav-item">
                        <a class="nav-link" href="/journey-planner">
                            <i class="fas fa-route me-1"></i>Journey Planner
                        </a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link" href="/history">
                            <i class="fas fa-history me-1"></i>History
                        </a>
                    </li>
                </ul>
                <ul class="navbar-nav">
                    {% if 'username' in session %}
                    <li class="nav-item dropdown">
                        <a class="nav-link dropdown-toggle" href="#" id="profileDropdown" role="button" data-bs-toggle="dropdown">
                            <i class="fas fa-user-circle me-1"></i>{{ session['name'] }}
                        </a>
                        <ul class="dropdown-menu dropdown-menu-end">
                            <li><a class="dropdown-item" href="/profile">
                                <i class="fas fa-user me-2"></i>Profile
                            </a></li>
                            <li><hr class="dropdown-divider"></li>
                            <li><a class="dropdown-item" href="/logout">
                                <i class="fas fa-sign-out-alt me-2"></i>Logout
                            </a></li>
                        </ul>
                    </li>
                    {% else %}
                    <li class="nav-item">
                        <a class="nav-link" href="/login">
                            <i class="fas fa-sign-in-alt me-1"></i>Login
                        </a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link" href="/register">
                            <i class="fas fa-user-plus me-1"></i>Register
                        </a>
                    </li>
                    {% endif %}
                </ul>
            </div>
        </div>
    </nav>

<div class="container my-4">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="alert alert-{{ category }} alert-dismissible fade show">
                        {{ message }}
                        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                    </div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        
        {{ content | safe }}
    </div>

    <!-- Bootstrap JS -->
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    </body>
</html>
"""

# User Authentication Routes
@app.route("/register", methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        username = request.form['username']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        
        result = register_user(name, username, password, confirm_password)
        if result == "success":
            flash('Account created successfully! Please login.', 'success')
            return redirect(url_for('login'))
        else:
            flash(result, 'danger')
            return redirect(url_for('register'))
    
    return render_template_string(BASE_HTML + """
    {% block content %}
    <div class="row justify-content-center">
        <div class="col-md-8">
            <div class="card shadow-sm">
                <div class="card-header card-header-bg text-white">
                    <h4 class="mb-0"><i class="fas fa-user-plus me-2"></i>Create New Account</h4>
                </div>
                <div class="card-body">
                    <form method="POST" action="/register">
                        <div class="row mb-3">
                            <div class="col-md-6">
                                <label for="name" class="form-label">Full Name</label>
                                <input type="text" class="form-control" id="name" name="name" required>
                            </div>
                            <div class="col-md-6">
                                <label for="username" class="form-label">Username</label>
                                <input type="text" class="form-control" id="username" name="username" required>
                            </div>
                        </div>
                        <div class="row mb-3">
                            <div class="col-md-6">
                                <label for="password" class="form-label">Password</label>
                                <input type="password" class="form-control" id="password" name="password" required>
                            </div>
                            <div class="col-md-6">
                                <label for="confirm_password" class="form-label">Confirm Password</label>
                                <input type="password" class="form-control" id="confirm_password" name="confirm_password" required>
                            </div>
                        </div>
                        <div class="d-grid gap-2">
                            <button type="submit" class="btn btn-warning btn-lg text-white">
                                <i class="fas fa-user-plus me-2"></i>Create Account
                            </button>
                        </div>
                    </form>
                    <div class="mt-3 text-center">
                        <p class="mb-0">Already have an account? <a href="/login">Login here</a></p>
                    </div>
                </div>
            </div>
        </div>
    </div>
    {% endblock %}
    """)

@app.route("/login", methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        user = login_user(username, password)
        if user:
            session['user_id'] = user[0]
            session['username'] = user[2]
            session['name'] = user[1]
            flash('Login successful!', 'success')
            return redirect(url_for('home'))
        else:
            flash('Invalid username or password', 'danger')
    
    return render_template_string(BASE_HTML + """
    {% block content %}
    <div class="row justify-content-center">
        <div class="col-md-6">
            <div class="card shadow-sm">
                <div class="card-header card-header-bg text-white">
                    <h4 class="mb-0"><i class="fas fa-sign-in-alt me-2"></i>Login to Your Account</h4>
                </div>
                <div class="card-body">
                    <form method="POST" action="/login">
                        <div class="mb-3">
                            <label for="username" class="form-label">Username</label>
                            <input type="text" class="form-control" id="username" name="username" required>
                        </div>
                        <div class="mb-3">
                            <label for="password" class="form-label">Password</label>
                            <input type="password" class="form-control" id="password" name="password" required>
                        </div>
                        <div class="d-grid gap-2">
                            <button type="submit" class="btn btn-warning btn-lg text-white">
                                <i class="fas fa-sign-in-alt me-2"></i>Login
                            </button>
                        </div>
                    </form>
                    <div class="mt-3 text-center">
                        <p class="mb-0">Don't have an account? <a href="/register">Register here</a></p>
                        <p class="mb-0"><a href="#">Forgot your password?</a></p>
                    </div>
                </div>
            </div>
        </div>
    </div>
    {% endblock %}
    """)

@app.route("/logout")
def logout():
    session.clear()
    flash('You have been logged out', 'info')
    return redirect(url_for('journey_planner'))
@app.route("/home")
def home():
    if 'user_id' not in session:
        flash('Please login to access the home page', 'warning')
        return redirect(url_for('login'))
    
    intro_content = """
    <div class="text-center">
        <h1>Welcome to Journey Planner TN</h1>
        <p class="lead">
            This platform helps you find the best routes across Tamil Nadu using bus services.
            Use the Journey Planner to search for your trip, or view your past searches in the History page.
        </p>
        <a href="/journey-planner" class="btn btn-warning text-white btn-lg mt-3">
            <i class="fas fa-route me-2"></i>Start Planning
        </a>
    </div>
    """
    return render_template_string(BASE_HTML, content=intro_content)

@app.route("/profile")
def profile():
    if 'user_id' not in session:
        flash('Please login to view your profile', 'warning')
        return redirect(url_for('login'))
    
    user = get_user_profile(session['user_id'])
    if not user:
        flash('User not found', 'danger')
        return redirect(url_for('journey_planner'))
    
    return render_template_string(BASE_HTML + f"""
    <div class="card shadow-sm">
        <div class="card-header card-header-bg text-white">
            <h4 class="mb-0"><i class="fas fa-user me-2"></i>User Profile</h4>
        </div>
        <div class="card-body">
            <div class="row mb-4">
                <div class="col-md-3 text-center">
                    <div class="bg-light rounded-circle p-4 mb-3" style="font-size: 3rem;">
                        <i class="fas fa-user text-secondary"></i>
                    </div>
                    <h5>{user[1]}</h5>
                    <p class="text-muted">@{user[2]}</p>
                </div>
                <div class="col-md-9">
                    <div class="card mb-3">
                        <div class="card-body">
                            <h5><i class="fas fa-info-circle me-2 text-warning"></i>Account Information</h5>
                            <hr>
                            <div class="row">
                                <div class="col-md-6">
                                    <p><strong>Name:</strong> {user[1]}</p>
                                    <p><strong>Username:</strong> {user[2]}</p>
                                </div>
                                <div class="col-md-6">
                                    <p><strong>Member since:</strong> {user[4]}</p>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    """)

@app.route("/history")
def history():
    if 'user_id' not in session:
        flash('Please login to view your history', 'warning')
        return redirect(url_for('login'))
    
    history_items = get_user_history(session['user_id'])
    
    return render_template_string(BASE_HTML + """
    {% block content %}
    <div class="card shadow-sm">
        <div class="card-header card-header-bg text-white">
            <h4 class="mb-0"><i class="fas fa-history me-2"></i>Journey History</h4>
        </div>
        <div class="card-body">
            {% if not history_items %}
            <div class="text-center py-5">
                <i class="fas fa-history fa-3x text-muted mb-3"></i>
                <h5>No journey history found</h5>
                <p class="text-muted">Your search history will appear here after you plan journeys</p>
                <a href="/journey-planner" class="btn btn-warning mt-3 text-white">
                    <i class="fas fa-route me-2"></i>Plan a Journey
                </a>
            </div>
            {% else %}
            <div class="list-group">
                {% for item in history_items %}
                <div class="list-group-item">
                    <div class="d-flex justify-content-between">
                        <div>
                            <h5 class="mb-1">{{ item[2] }} → {{ item[3] }}</h5>
                            <small class="text-muted">{{ item[6] }} | Mode: {{ item[5] }}</small>
                        </div>
                        <div>
                            <a href="/view-history/{{ item[0] }}" class="btn btn-sm btn-outline-warning">
                                <i class="fas fa-eye me-1"></i>View
                            </a>
                        </div>
                    </div>
                </div>
                {% endfor %}
            </div>
            {% endif %}
        </div>
    </div>
    {% endblock %}
    """, history_items=history_items)

@app.route("/view-history/<int:history_id>")
def view_history(history_id):
    if 'user_id' not in session:
        flash('Please login to view history', 'warning')
        return redirect(url_for('login'))
    
    conn = sqlite3.connect('transport.db')
    c = conn.cursor()
    c.execute("SELECT * FROM history WHERE id = ? AND user_id = ?", (history_id, session['user_id']))
    history_item = c.fetchone()
    conn.close()
    
    if not history_item:
        flash('History item not found', 'danger')
        return redirect(url_for('history'))
    
    # Parse the stored results
    results = eval(history_item[6]) if history_item[6] else []
    
    return render_template_string(BASE_HTML + """
    {% block content %}
    <div class="card shadow-sm mb-4">
        <div class="card-header card-header-bg text-white">
            <h4 class="mb-0"><i class="fas fa-history me-2"></i>Journey Details</h4>
        </div>
        <div class="card-body">
            <div class="row mb-3">
                <div class="col-md-4">
                    <p><strong>From:</strong> {history_item[2]}</p>
                </div>
                <div class="col-md-4">
                    <p><strong>To:</strong> {history_item[3]}</p>
                </div>
                <div class="col-md-4">
                    <p><strong>Date:</strong> {history_item[4]}</p>
                </div>
            </div>
            <a href="/journey-planner?source={history_item[2]}&destination={history_item[3]}&date={history_item[4]}&mode={history_item[5]}" 
               class="btn btn-warning text-white">
               <i class="fas fa-redo me-2"></i>Search Again
            </a>
        </div>
    </div>
    
    <div class="card shadow-sm">
        <div class="card-header bg-secondary text-white">
            <h5 class="mb-0">Original Results</h5>
        </div>
        <div class="card-body">
            {RESULTS_HTML.replace('{{ source_loc }}', history_item[2])
                         .replace('{{ destination_loc }}', history_item[3])
                         .replace('{{ date_str }}', history_item[4])
                         .replace('{{ results }}', results)
                         .replace('{{ error }}', '')
                         .replace('{{ sort_js }}', SORT_JS)}
        </div>
    </div>
    {% endblock %}
    """)


@app.route("/")
def index():
    if 'user_id' in session:
        return redirect(url_for('home'))  # Logged in → Go to home
    else:
        return redirect(url_for('login'))  # Not logged in → Go to login

@app.route("/journey-planner", methods=['GET'])
def journey_planner():
    if 'user_id' not in session:
        flash('Please login to access the journey planner', 'warning')
        return redirect(url_for('login'))
    # Pre-fill form if parameters are passed
    source = request.args.get('source', '')
    destination = request.args.get('destination', '')
    date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    mode = request.args.get('mode', 'both')
    
    # Create the filled-in form HTML
    filled_form = INDEX_HTML.replace(
        'id="source"', f'id="source" value="{source}"').replace(
        'id="destination"', f'id="destination" value="{destination}"').replace(
        'id="date"', f'id="date" value="{date}"').replace(
        'value="both" selected', f'value="{mode}" selected')
    
    # Render using the base template
    return render_template_string(
        BASE_HTML,
        content=filled_form
    )

# HTML templates
INDEX_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Journey Planner TN</title>
    <!-- Tailwind CSS CDN -->
    <script src="https://cdn.tailwindcss.com"></script>
    <!-- Font Awesome for icons -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.2/css/all.min.css">
    <!-- Google Fonts: Poppins -->
    <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        /* Custom background with image and overlay */
        body {
            background: linear-gradient(rgba(0, 0, 0, 0.5), rgba(0, 0, 0, 0.5)), url('https://images.unsplash.com/photo-1600585154340-be6161a56a0c?auto=format&fit=crop&w=1920&q=80');
            background-size: cover;
            background-position: center;
            background-attachment: fixed;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            font-family: 'Poppins', sans-serif;
            color: #333;
        }
        /* Form container styling */
        .form-container {
            background: #ffffff;
            border-radius: 1.5rem;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
            padding: 2.5rem;
            max-width: 700px;
            width: 90%;
            animation: slideIn 0.5s ease-out;
        }
        /* Slide-in animation */
        @keyframes slideIn {
            from {
                opacity: 0;
                transform: translateY(50px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        /* Input and button animations */
        .form-input, .form-select, .form-btn {
            transition: all 0.3s ease-in-out;
        }
        .form-input:focus, .form-select:focus {
            border-color: #f59e0b;
            box-shadow: 0 0 0 4px rgba(245, 158, 11, 0.2);
            transform: scale(1.02);
        }
        .form-btn {
            background: linear-gradient(to right, #f59e0b, #d97706);
            border: none;
            padding: 0.75rem 1.5rem;
        }
        .form-btn:hover {
            background: linear-gradient(to right, #d97706, #b45309);
            transform: translateY(-3px);
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
        }
        /* Logo animation */
        .logo-icon {
            transition: transform 0.3s ease;
        }
        .logo-icon:hover {
            transform: rotate(360deg);
        }
        /* Responsive adjustments */
        @media (max-width: 640px) {
            .form-container {
                padding: 1.5rem;
                margin: 1rem;
            }
            h1 {
                font-size: 1.75rem;
            }
            .form-btn {
                padding: 0.5rem 1rem;
            }
        }
    </style>
</head>
<body>
    <div class="form-container">
    <div class="text-center mb-8">
        <h1 class="text-4xl font-bold text-gray-800 flex items-center justify-center">
            <i class="fas fa-compass mr-3 text-amber-500 logo-icon"></i> Journey Planner TN
        </h1>
        <p class="text-gray-600 mt-2 text-lg">Discover the best routes across Tamil Nadu!</p>
    </div>
    <form method="post" action="/search" class="space-y-6">
        <div>
            <label for="source" class="block text-sm font-semibold text-gray-700 mb-2">
                <i class="fas fa-map-pin mr-2 text-amber-500"></i> From Where?
            </label>
            <input type="text" class="form-input w-full px-5 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring" 
                   id="source" name="source" required placeholder="e.g., Head Post Office, Chennai" 
                   aria-label="Source address or place">
        </div>
        <div>
            <label for="destination" class="block text-sm font-semibold text-gray-700 mb-2">
                <i class="fas fa-flag mr-2 text-amber-500"></i> To Where?
            </label>
            <input type="text" class="form-input w-full px-5 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring" 
                   id="destination" name="destination" required placeholder="e.g., Rajiv Gandhi Hospital" 
                   aria-label="Destination address or place">
        </div>
        <div>
            <label for="date" class="block text-sm font-semibold text-gray-700 mb-2">
                <i class="fas fa-calendar-day mr-2 text-amber-500"></i> When?
            </label>
            <input type="date" class="form-input w-full px-5 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring" 
                   id="date" name="date" required aria-label="Journey date">
        </div>
        <div>
            <label for="mode" class="block text-sm font-semibold text-gray-700 mb-2">
                <i class="fas fa-train mr-2 text-amber-500"></i> How?
            </label>
            <select class="form-select w-full px-5 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring" 
                    id="mode" name="mode" aria-label="Preferred travel mode">
                <option value="bus">Bus </option>
            </select>
        </div>
        <button type="submit" class="form-btn w-full py-3 px-6 text-white font-semibold rounded-lg shadow-lg">
            <i class="fas fa-search-location mr-2"></i> Find Your Route
        </button>
    </form>
</div>
</body>
</html>
"""

RESULTS_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Search Results - TN Transport Finder</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  {{ sort_js|safe }}
</head>
<body class="bg-light">
<div class="container py-4">
  <h1 class="mb-4">Results for "{{ source_loc }}" → "{{ destination_loc }}" on {{ date_str }}</h1>

  {% if source_city and source_city_coords %}
    <p><strong>Source city:</strong> {{ source_city }} — city-center coords: {{ source_city_coords[0]|round(6) }}, {{ source_city_coords[1]|round(6) }}</p>
    {% if source_bus_stand_coords %}
      <p><strong>Source main bus stand:</strong> {{ source_bus_stand_name }} at {{ source_bus_stand_coords[0]|round(6) }}, {{ source_bus_stand_coords[1]|round(6) }}</p>
    {% endif %}
  {% endif %}
  {% if destination_city and dest_city_coords %}
    <p><strong>Destination city:</strong> {{ destination_city }} — city-center coords: {{ dest_city_coords[0]|round(6) }}, {{ dest_city_coords[1]|round(6) }}</p>
    {% if dest_bus_stand_coords %}
      <p><strong>Destination main bus stand:</strong> {{ dest_bus_stand_name }} at {{ dest_bus_stand_coords[0]|round(6) }}, {{ dest_bus_stand_coords[1]|round(6) }}</p>
    {% endif %}
  {% endif %}

  <div class="mb-4">
    <a href="/journey-planner" class="btn btn-secondary">&larr; New Search</a>
  </div>
  {% if error %}
    <div class="alert alert-warning">{{ error }}</div>
  {% endif %}
  
  <!-- Fare Information Card -->
  <div class="card mb-4">
    <div class="card-header">
      <h5>Fare Information</h5>
    </div>
    <div class="card-body">
      <h6>🚖 Auto Rickshaw Fare</h6>
      <ul>
        <li>Minimum fare: ₹50 for the first 1.8 km</li>
        <li>After that: ₹18 per km</li>
        <li>Waiting charge: ₹1.50 per minute</li>
        <li>Night surcharge (11 PM – 5 AM): 50% extra</li>
      </ul>
      
      <h6>🚗 Cab Fare (Standard taxis or app-based like Ola/Uber)</h6>
      <ul>
        <li>Base fare: ₹100 (includes 1–2 km depending on service)</li>
        <li>Per km after base: ₹15–₹20</li>
        <li>Waiting charge: ₹100–₹120 per hour</li>
        <li>Night surcharge: 50% extra</li>
      </ul>
    </div>
  </div>
  
  {% if results %}
    <table class="table table-striped" id="resultsTable">
      <thead>
        <tr>
          <th>Provider</th>
          <th>Operator / Train</th>
          <th class="sortable" data-type="time">Departure</th>
          <th class="sortable" data-type="time">Arrival</th>
          <th>Duration</th>
          <th class="sortable" data-type="number">Fare</th>
          <th>Class</th>
          <th>Route</th>
          <th>Book</th>
        </tr>
      </thead>
      <tbody>
      {% for r in results %}
        <tr>
          <td data-sort="{{ r.provider }}">{{ r.provider }}</td>
          <td data-sort="{{ r.operator }}">
            {% if r.train_number %}{{ r.train_number }} - {% endif %}
            {{ r.operator or r.train_name }}
          </td>
          <td data-sort="{{ r.departure }}">{{ r.departure }}</td>
          <td data-sort="{{ r.arrival }}">{{ r.arrival }}</td>
          <td data-sort="{{ r.duration }}">{{ r.duration }}</td>
          <td data-sort="{{ r.fare }}">{{ r.fare }}</td>
          <td>{{ r.class if r.class else 'N/A' }}</td>
          <td>
            <button class="btn btn-sm btn-info toggle-route">
              ▼ Show Route
            </button>
          </td>
          <td>
            {% if r.booking_link %}
              <a href="{{ r.booking_link }}" class="btn btn-sm btn-primary" target="_blank">Book</a>
            {% else %}
              <span class="text-muted">N/A</span>
            {% endif %}
          </td>
        </tr>
        <tr style="display: none;">
          <td colspan="9">
            {{ r.route_details|safe if r.route_details else 'Route details not available' }}
          </td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  {% else %}
    <div class="alert alert-info">No options found.</div>
  {% endif %}
</div>

<!-- Bootstrap JS for better interaction -->
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
"""

SORT_JS = r"""
<script>
// Simple table sorter: assumes <table id="resultsTable">, <th data-type="time|number|string"> headers.
document.addEventListener('DOMContentLoaded', function(){
    const getCellValue = (tr, idx) => tr.children[idx].getAttribute('data-sort') || tr.children[idx].innerText;
    const comparer = function(idx, asc, type) {
        return function(a, b) {
            let v1 = getCellValue(asc ? a : b, idx);
            let v2 = getCellValue(asc ? b : a, idx);
            if(type==='number'){
                let n1 = parseFloat(v1.replace(/[^0-9\.]/g,'')) || 0;
                let n2 = parseFloat(v2.replace(/[^0-9\.]/g,'')) || 0;
                return n1 - n2;
            } else if(type==='time'){
                // parse HH:MM or H:MM
                const parseTime = s => {
                    const m = /(\d{1,2}):(\d{2})/.exec(s);
                    if(m){
                        return parseInt(m[1])*60 + parseInt(m[2]);
                    }
                    return 0;
                };
                return parseTime(v1) - parseTime(v2);
            } else {
                // string
                return v1.toString().localeCompare(v2);
            }
        };
    };
    document.querySelectorAll('th.sortable').forEach(function(th){
        th.addEventListener('click', function(){
            const table = th.closest('table');
            const tbody = table.querySelector('tbody');
            Array.from(table.querySelectorAll('th')).forEach(th2 => th2.classList.remove('asc','desc'));
            let asc = !th.classList.contains('asc');
            th.classList.toggle('asc', asc);
            th.classList.toggle('desc', !asc);
            const idx = Array.prototype.indexOf.call(th.parentNode.children, th);
            const type = th.getAttribute('data-type') || 'string';
            const rows = Array.from(tbody.querySelectorAll('tr'));
            rows.sort(comparer(idx, asc, type));
            rows.forEach(r => tbody.appendChild(r));
        });
    });
    
    // Toggle route details
    document.querySelectorAll('.toggle-route').forEach(button => {
        button.addEventListener('click', function() {
            const detailsRow = this.closest('tr').nextElementSibling;
            if (detailsRow.style.display === 'none') {
                detailsRow.style.display = 'table-row';
                this.textContent = '▲ Hide Route';
            } else {
                detailsRow.style.display = 'none';
                this.textContent = '▼ Show Route';
            }
        });
    });
});
</script>
<style>
th.sortable { cursor: pointer; }
th.asc::after { content: " ▲"; }
th.desc::after { content: " ▼"; }
.route-details {
    padding: 10px;
    background: #f8f9fa;
    border-radius: 5px;
    font-size: 0.9rem;
    line-height: 1.4;
}
.route-step {
    padding: 4px 0;
    margin-bottom: 0;
    border-left: none !important;
}
.route-step.you { border-color: #dc3545; }
.route-step.walk { border-color: #28a745; }
.route-step.auto { border-color: #ffc107; }
.route-step.cab { border-color: #17a2b8; }
.route-step.bus { border-color: #6610f2; }
</style>
"""

@app.route("/search", methods=["POST"])
def search():
    source_input = request.form.get("source", "").strip()
    dest_input = request.form.get("destination", "").strip()
    date_input = request.form.get("date", "").strip()  # 'YYYY-MM-DD'
    mode = request.form.get("mode", "both")

    logging.info(f"Search requested: '{source_input}' -> '{dest_input}' on {date_input}")

    if not source_input or not dest_input or not date_input:
        return render_template_string(RESULTS_HTML,
                                      source_loc=source_input, destination_loc=dest_input,
                                      date_str=date_input, results=[], error="Missing input",
                                      sort_js=SORT_JS,
                                      source_city=None, source_city_coords=None, source_bus_stand_coords=None,
                                      destination_city=None, dest_city_coords=None, dest_bus_stand_coords=None)

    # Geocode source and destination
    source_coords = get_coordinates(source_input)
    dest_coords = get_coordinates(dest_input)
    if not source_coords or not dest_coords:
        return render_template_string(RESULTS_HTML,
                                      source_loc=source_input, destination_loc=dest_input,
                                      date_str=date_input, results=[], error="Could not geocode source or destination",
                                      sort_js=SORT_JS,
                                      source_city=None, source_city_coords=None, source_bus_stand_coords=None,
                                      destination_city=None, dest_city_coords=None, dest_bus_stand_coords=None)

    logging.info(f"Coordinates: Source={source_coords}, Destination={dest_coords}")

    # Derive source city
    if ',' in source_input:
        src_input_city = extract_city(source_input)
        if src_input_city:
            source_city = src_input_city
            logging.info(f"Using extracted source city from input: '{source_city}'")
        else:
            rev = get_city_from_coords(source_coords)
            source_city = rev if rev else ""
            logging.info(f"Extracted source empty; using reverse-geocoded source city: '{source_city}'")
    else:
        rev = get_city_from_coords(source_coords)
        source_city = rev if rev else ""
        logging.info(f"No comma in source input; using reverse-geocoded source city: '{source_city}'")

    # Derive destination city
    if ',' in dest_input:
        dst_input_city = extract_city(dest_input)
        if dst_input_city:
            destination_city = dst_input_city
            logging.info(f"Using extracted destination city from input: '{destination_city}'")
        else:
            rev2 = get_city_from_coords(dest_coords)
            destination_city = rev2 if rev2 else ""
            logging.info(f"Extracted destination empty; using reverse-geocoded destination city: '{destination_city}'")
    else:
        rev2 = get_city_from_coords(dest_coords)
        destination_city = rev2 if rev2 else ""
        logging.info(f"No comma in dest input; using reverse-geocoded destination city: '{destination_city}'")

    # Normalize lowercase for lookups
    source_city_l = source_city.lower().strip()
    dest_city_l = destination_city.lower().strip()
    logging.info(f"Derived source city/town: '{source_city_l}', destination city/town: '{dest_city_l}'")

    # Geocode the extracted city/town names to get city-center coordinates
    source_city_coords = None
    dest_city_coords = None
    if source_city:
        ccoords = get_coordinates(source_city)
        if ccoords:
            source_city_coords = ccoords
            logging.info(f"Source city-center coords: {source_city_coords}")
    if destination_city:
        ccoords2 = get_coordinates(destination_city)
        if ccoords2:
            dest_city_coords = ccoords2
            logging.info(f"Destination city-center coords: {dest_city_coords}")

    # Find best bus stands using new identification system
    source_bus_stand_info = None
    dest_bus_stand_info = None
    
    # For source bus stand
    if source_city and source_city_coords:
        source_bus_stand_info = find_best_bus_stand(source_city, source_city_coords)
        if source_bus_stand_info:
            source_bus_stand_coords = source_bus_stand_info['coords']
            source_bus_stand_name = source_bus_stand_info['name']
            logging.info(f"Found source bus stand: {source_bus_stand_name} at {source_bus_stand_coords}")
        else:
            source_bus_stand_coords = None
            source_bus_stand_name = None
            logging.info("No suitable source bus stand found")
    else:
        source_bus_stand_coords = None
        source_bus_stand_name = None
        logging.info("Skipping source bus stand search - missing city name or coordinates")

    # For destination bus stand
    if destination_city and dest_city_coords:
        dest_bus_stand_info = find_best_bus_stand(destination_city, dest_city_coords)
        if dest_bus_stand_info:
            dest_bus_stand_coords = dest_bus_stand_info['coords']
            dest_bus_stand_name = dest_bus_stand_info['name']
            logging.info(f"Found destination bus stand: {dest_bus_stand_name} at {dest_bus_stand_coords}")
        else:
            dest_bus_stand_coords = None
            dest_bus_stand_name = None
            logging.info("No suitable destination bus stand found")
    else:
        dest_bus_stand_coords = None
        dest_bus_stand_name = None
        logging.info("Skipping destination bus stand search - missing city name or coordinates")

    # Prepare date formats
    try:
        date_obj = datetime.strptime(date_input, "%Y-%m-%d")
    except ValueError:
        return render_template_string(RESULTS_HTML,
                                      source_loc=source_input, destination_loc=dest_input,
                                      date_str=date_input, results=[], error="Invalid date format",
                                      sort_js=SORT_JS,
                                      source_city=source_city, source_city_coords=source_city_coords, source_bus_stand_coords=source_bus_stand_coords,
                                      destination_city=destination_city, dest_city_coords=dest_city_coords, dest_bus_stand_coords=dest_bus_stand_coords)
    date_bus_abhibus = date_obj.strftime("%d-%m-%Y")  # e.g. 27-06-2025
    date_redbus = date_obj.strftime("%d-%b-%Y")       # e.g. 27-Jun-2025
    date_tnstc = date_obj.strftime("%d/%m/%Y")        # e.g. 27/06/2025


    results = []
    seen = set()





    
    # For bus routes
    if source_bus_stand_coords and dest_bus_stand_coords:
        hub_to_hub_distance = geodesic(source_bus_stand_coords, dest_bus_stand_coords).km
        hub_to_hub_name = f"{source_bus_stand_name} to {dest_bus_stand_name}"
        logging.info(f"Bus hub-to-hub distance: {hub_to_hub_distance:.1f} km")
    


    # 1) Bus searches if mode includes bus
    if mode in ('bus','both'):
        # TNSTC: try combinations of city and "Bus Stand"
        tnstc_source_try = []
        tnstc_dest_try = []
        if source_city:
            tnstc_source_try.append(source_city)
            if source_bus_stand_info:
                tnstc_source_try.append(f"{source_city} Bus Stand")
        if destination_city:
            tnstc_dest_try.append(destination_city)
            if dest_bus_stand_info:
                tnstc_dest_try.append(f"{destination_city} Bus Stand")

        # Attempt TNSTC with each combination until results found
        tn_found = False
        for sc in tnstc_source_try:
            for dc in tnstc_dest_try:
                logging.info(f"Checking TNSTC direct schedules for '{sc}' -> '{dc}' on {date_tnstc}")
                tn_results = get_tnstc_bus_schedules(sc.lower().strip(), dc.lower().strip(), date_tnstc)
                if tn_results:
                    tn_found = True
                    for r in tn_results:
                        key = (r['provider'], r['operator'], r['departure'], r['arrival'])
                        if key in seen: continue
                        seen.add(key)
                        
                        # Build route steps
                        route_steps = build_route_steps(
                            source_input,
                            dest_input,
                            source_coords, 
                            source_bus_stand_coords if source_bus_stand_coords else source_city_coords, 
                            source_bus_stand_name or source_city,
                            hub_to_hub_distance or 0,
                            hub_to_hub_name or "Bus Journey",
                            dest_bus_stand_coords if dest_bus_stand_coords else dest_city_coords, 
                            dest_bus_stand_name or destination_city,
                            dest_coords,
                            is_bus=True,
                            departure_time=r['departure']
                        )
                        
                        # Calculate total cost and update route steps
                        total_cost, route_steps = calculate_total_fare(route_steps, r['fare'])
                        
                        entry = {
                            'provider': r['provider'],
                            'operator': r['operator'],
                            'departure': r['departure'],
                            'arrival': r['arrival'],
                            'duration': r.get('duration',''),
                            'fare': r.get('fare',''),
                            'total_cost': total_cost,
                            'route_details': generate_route_details(route_steps),
                            'booking_link': "https://www.tnstc.in"
                        }
                        results.append(entry)
                    break
            if tn_found:
                break
        if not tn_found:
            logging.info(f"TNSTC: no schedules found for any combination for '{source_city}' -> '{destination_city}'")

        # AbhiBus: try source_city then "Bus Stand", same for dest
        abhi_src_id = None
        abhi_dest_id = None
        # Try source variants
        if source_city:
            for sc in [source_city] + ([f"{source_city} Bus Stand"] if source_bus_stand_info else []):
                sc_l = sc.lower().strip()
                abhi_src_id = get_abhibus_city_id(sc_l)
                if abhi_src_id:
                    logging.info(f"AbhiBus: found city ID for source '{sc_l}': {abhi_src_id}")
                    break
        # Try dest variants
        if destination_city:
            for dc in [destination_city] + ([f"{destination_city} Bus Stand"] if dest_bus_stand_info else []):
                dc_l = dc.lower().strip()
                abhi_dest_id = get_abhibus_city_id(dc_l)
                if abhi_dest_id:
                    logging.info(f"AbhiBus: found city ID for dest '{dc_l}': {abhi_dest_id}")
                    break
        if abhi_src_id and abhi_dest_id:
            url_ab = f"https://www.abhibus.com/bus_search/{source_city.lower().strip()}/{abhi_src_id}/{destination_city.lower().strip()}/{abhi_dest_id}/{date_bus_abhibus}/O"
            logging.info(f"Checking AbhiBus direct schedules with URL: {url_ab}")
            abhi_results = get_abhibus_schedules(url_ab)
            for r in abhi_results:
                key = (r['provider'], r['operator'], r['departure'], r['arrival'])
                if key in seen: continue
                seen.add(key)
                
                # Build route steps
                route_steps = build_route_steps(
                    source_input,
                    dest_input,
                    source_coords, 
                    source_bus_stand_coords if source_bus_stand_coords else source_city_coords, 
                    source_bus_stand_name or source_city,
                    hub_to_hub_distance or 0,
                    hub_to_hub_name or "Bus Journey",
                    dest_bus_stand_coords if dest_bus_stand_coords else dest_city_coords, 
                    dest_bus_stand_name or destination_city,
                    dest_coords,
                    is_bus=True,
                    departure_time=r['departure']
                )
                
                # Calculate total cost and update route steps
                total_cost, route_steps = calculate_total_fare(route_steps, r['fare'])
                
                entry = {
                    'provider': r['provider'],
                    'operator': r['operator'],
                    'departure': r['departure'],
                    'arrival': r['arrival'],
                    'duration': r.get('duration',''),
                    'fare': r.get('fare',''),
                    'total_cost': total_cost,
                    'route_details': generate_route_details(route_steps),
                    'booking_link': r.get('booking_url')
                }
                results.append(entry)
        else:
            logging.info(f"AbhiBus fallback: could not obtain city IDs for '{source_city}' or '{destination_city}'")

        # RedBus: use city slugs
        try:
            src_rb = source_city.lower().strip().replace(' ', '-')
            dst_rb = destination_city.lower().strip().replace(' ', '-')
            rb_search_url = f"https://www.redbus.in/bus-tickets/{src_rb}-to-{dst_rb}/?fromCityName={source_city}&toCityName={destination_city}&onward={date_redbus}&doj={date_redbus}"
            logging.info(f"Checking RedBus direct schedules for {source_city.lower().strip()} -> {destination_city.lower().strip()} on {date_redbus}")
            rb_results = get_redbus_schedules(rb_search_url)
            for r in rb_results:
                key = (r['provider'], r['operator'], r.get('departure',''), r.get('arrival',''))
                if key in seen: continue
                seen.add(key)
                
                # Build route steps
                route_steps = build_route_steps(
                    source_input,
                    dest_input,
                    source_coords, 
                    source_bus_stand_coords if source_bus_stand_coords else source_city_coords, 
                    source_bus_stand_name or source_city,
                    hub_to_hub_distance or 0,
                    hub_to_hub_name or "Bus Journey",
                    dest_bus_stand_coords if dest_bus_stand_coords else dest_city_coords, 
                    dest_bus_stand_name or destination_city,
                    dest_coords,
                    is_bus=True,
                    departure_time=r.get('departure')
                )
                
                # Calculate total cost and update route steps
                total_cost, route_steps = calculate_total_fare(route_steps, r.get('fare'))
                
                entry = {
                    'provider': r['provider'],
                    'operator': r['operator'],
                    'departure': r.get('departure',''),
                    'arrival': r.get('arrival',''),
                    'duration': r.get('duration',''),
                    'fare': r.get('fare',''),
                    'total_cost': total_cost,
                    'route_details': generate_route_details(route_steps),
                    'booking_link': rb_search_url
                }
                results.append(entry)
        except Exception as e:
            logging.error(f"RedBus error: {e}")

   
    if not results:
        return render_template_string(RESULTS_HTML,
                                      source_loc=source_input, destination_loc=dest_input,
                                      date_str=date_input, results=[], error="No routes found with the current logic.",
                                      sort_js=SORT_JS,
                                      source_city=source_city, source_city_coords=source_city_coords, source_bus_stand_coords=source_bus_stand_coords,
                                      destination_city=destination_city, dest_city_coords=dest_city_coords, dest_bus_stand_coords=dest_bus_stand_coords)
    # Save to history
    if 'user_id' in session:
        conn = sqlite3.connect('transport.db')
        c = conn.cursor()
        c.execute("INSERT INTO history (user_id, source, destination, date, mode, results) VALUES (?, ?, ?, ?, ?, ?)",
                 (session['user_id'], source_input, dest_input, date_input, mode, str(results)))
        conn.commit()
        conn.close()
    return render_template_string(RESULTS_HTML,
                                  source_loc=source_input, destination_loc=dest_input,
                                  date_str=date_input, results=results, error=None,
                                  sort_js=SORT_JS,
                                  source_city=source_city, source_city_coords=source_city_coords, source_bus_stand_coords=source_bus_stand_coords,
                                  destination_city=destination_city, dest_city_coords=dest_city_coords, dest_bus_stand_coords=dest_bus_stand_coords)

if __name__ == "__main__":
    # Load MTC routes on startup
    load_mtc_routes()
    app.run(host='0.0.0.0', port=5000, debug=True)