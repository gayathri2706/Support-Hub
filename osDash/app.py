from http import server
from flask import Flask, jsonify, render_template, request, send_from_directory, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail, Message
from sqlalchemy import text
from datetime import datetime
import random
import json
import os
from werkzeug.utils import secure_filename
import smtplib
import threading

# Load configuration from JSON file
with open("config.json") as config_file:
    config = json.load(config_file)

# Construct database URI dynamically
db_config = config["database"]


app = Flask(__name__)

# Configure the database connection
app.config['SQLALCHEMY_DATABASE_URI'] = f"{db_config['driver']}://{db_config['username']}:{db_config['password']}@{db_config['host']}/{db_config['database']}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False


# Print the final connection URI for verification
print(app.config['SQLALCHEMY_DATABASE_URI'])

db = SQLAlchemy(app) 

# Temporary user information (for testing)
current_user = {
     "username": "admin",
 }

# File Upload Configuration
UPLOAD_FOLDER = os.path.abspath(config["upload"]["folder"])
ALLOWED_EXTENSIONS = set(config["upload"]["allowed_extensions"])
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = config["upload"]["max_content_length"]

# Ensure directories exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
USER_FILES_FOLDER = os.path.join(UPLOAD_FOLDER, "user_files")
os.makedirs(USER_FILES_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Max upload size = 16MB

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/api/upload', methods=['POST'])
def upload_file():
    """Handle file uploads."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        return jsonify({'success': 'File uploaded', 'filename': filename}), 200

    return jsonify({'error': 'Invalid file type'}), 400

@app.route('/uploads/<filename>', methods=['GET'])
def serve_file(filename):
    """Serve uploaded files with download option."""
    try:
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=True)
    except FileNotFoundError:
        return jsonify({'error': 'File not found'}), 404

@app.route('/uploads/user_files/<filename>', methods=['GET'])
def serve_user_file(filename):
    """Serve uploaded user files with download option."""
    try:
        return send_from_directory(USER_FILES_FOLDER, filename, as_attachment=True)
    except FileNotFoundError:
        return jsonify({'error': 'File not found'}), 404

# Function to send emails in a separate thread
def send_email_async(app, msg):
    with app.app_context():
        try:
            mail.send(msg)
            print(f"Email sent successfully to {msg.recipients}")
        except Exception as e:
            print(f"Error sending email: {str(e)}")

# Flask-Mail Configuration
mail_config = config["mail"]
app.config.update(
    MAIL_SERVER=mail_config["server"],
    MAIL_PORT=mail_config["port"],
    MAIL_USE_TLS=mail_config["use_tls"],
    MAIL_USE_SSL=mail_config["use_ssl"],
    MAIL_USERNAME=mail_config["username"],
    MAIL_PASSWORD=mail_config["password"],
    MAIL_DEFAULT_SENDER=tuple(mail_config["default_sender"])
)

mail = Mail(app)

@app.route('/send-feedback', methods=['POST'])
def send_feedback():
    """Send feedback via email to dynamically selected admins."""
    try:
        name = request.form.get('name')
        email = request.form.get('email')
        ticket_id = request.form.get('ticket_id')
        experience = request.form.get('experience')

        if not all([name, email, ticket_id, experience]):
            return jsonify({'error': 'Missing required fields'}), 400

        # Fetch only selected admin emails
        admin_results = db.session.execute(text("SELECT email FROM assign_to WHERE is_admin = TRUE")).fetchall()
        admin_emails = [row[0] for row in admin_results]

        if not admin_emails:
            admin_emails = ["muralidharan@mpminfosoft.com"]  # Default admin email

        # Prepare email content
        msg = Message(
            subject=f"Feedback for Ticket ID: {ticket_id}",
            recipients=admin_emails,
            body=f"""
            Ticket ID: {ticket_id}
            Name: {name}
            Email: {email}
            Experience/Comments:\n{experience}
            """
        )
        
        # Send the email
        mail.send(msg)
        return jsonify({'success': 'Feedback sent successfully!'}), 200

    except Exception as e:
        print(f"Error occurred: {e}")
        return jsonify({'error': 'An unexpected error occurred'}), 500

@app.route('/')
def index():
    """Render the homepage with user authentication from Sandman using user_name."""
    username = request.args.get("user", "").replace(" ", "").lower()  # Get and normalize the username
    print(f"Received normalized username: {username}")  # Debug line

    if not username:
        return "Error: No username provided", 400

    # Handle super admin (admin keyword)
    if "admin" in username:
        foundries = [row[0] for row in db.session.execute(text("SELECT DISTINCT foundry_name FROM foundries")).fetchall()]
        tickets = db.session.execute(text("""
            SELECT ticket_id, DATE(date_created) AS date_created, DATE(resolved_time) AS resolved_time,
                   foundry_name, name, email, priority, issue, issue_description, status, comments, resolved_file
            FROM tickets
            ORDER BY COALESCE(resolved_time, date_created) DESC
        """)).fetchall()

        return render_template('index.html',
                               tickets=tickets,
                               foundries=foundries,
                               user_info=None,
                               current_user={"username": username, "is_admin": True},
                               dashboard_tickets=tickets)

    # Normalize and check user_name match
    persons_raw = db.session.execute(text("SELECT foundry_name, user_name, email, phone, is_admin FROM foundries")).fetchall()
    for foundry_name, user_name_db, email, phone, is_admin in persons_raw:
        normalized_user_name = str(user_name_db).replace(" ", "").lower() if user_name_db else ""
        
        if normalized_user_name == username:
            tickets = db.session.execute(text("""
                SELECT * FROM tickets WHERE foundry_name = :foundry_name
                ORDER BY COALESCE(resolved_time, date_created) DESC
            """), {'foundry_name': foundry_name}).fetchall()

            dashboard_tickets = []
            if is_admin:
                dashboard_tickets = db.session.execute(text("""
                    SELECT t.ticket_id, t.date_created, t.foundry_name, t.issue, t.issue_description, 
                           t.priority, t.status, t.resolved_time, t.attachment_file,
                           a.name AS assigned_to, a.email AS assigned_email
                    FROM tickets t
                    LEFT JOIN assign_to a ON t.assign_to = a.name
                    ORDER BY COALESCE(t.resolved_time, t.date_created) DESC
                """)).fetchall()

            return render_template('index.html',
                                   tickets=tickets,
                                   foundries=[foundry_name],
                                   user_info={"name": user_name_db, "email": email, "phone": phone},
                                   current_user={"username": user_name_db, "is_admin": bool(is_admin)},
                                   dashboard_tickets=dashboard_tickets if is_admin else None)
    
    # If we reach here, we didn't find a matching user - this was the missing return statement
    return "Error: Invalid user - could not find matching username", 401

@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    """
    Render the dashboard for super admin or foundry-level admin.
    """
    username = request.args.get("user")  # Get the username from URL or request

    if not username:
        return "Error: Username missing", 400

    print(f"Username received: {username}")  # Debugging line

    # Super admin: see all tickets
    if "admin" in username.lower():
        foundry_name = "All"
        # Query for all tickets (Super Admin)
        tickets = db.session.execute(text("""
            SELECT t.ticket_id, t.date_created, t.foundry_name, t.issue, t.issue_description, 
                   t.priority, t.status, t.resolved_time, t.attachment_file,
                   a.name AS assigned_to, a.email AS assigned_email
            FROM tickets t
            LEFT JOIN assign_to a ON t.assign_to = a.name
            ORDER BY COALESCE(t.resolved_time, t.date_created) DESC
        """)).fetchall()
    else:
        # Foundry admin: show tickets for their foundry only
        # Fix: Use user_name instead of username in the query
        result = db.session.execute(text("""
            SELECT DISTINCT foundry_name 
            FROM foundries 
            WHERE LOWER(person_name) = LOWER(:username) AND is_admin = 1
        """), {'username': username}).fetchone()

        # If not found by person_name, try with user_name
        if not result:
            result = db.session.execute(text("""
                SELECT DISTINCT foundry_name 
                FROM foundries 
                WHERE LOWER(user_name) = LOWER(:username) AND is_admin = 1
            """), {'username': username}).fetchone()

        # If the result is None or empty, deny access
        if not result:
            return f"Unauthorized: No admin rights found for username '{username}'", 403

        foundry_name = result[0]
        
        # Query for tickets in the foundry
        tickets = db.session.execute(text("""
            SELECT t.ticket_id, t.date_created, t.foundry_name, t.issue, t.issue_description, 
                   t.priority, t.status, t.resolved_time, t.attachment_file,
                   a.name AS assigned_to, a.email AS assigned_email
            FROM tickets t
            LEFT JOIN assign_to a ON t.assign_to = a.name
            WHERE t.foundry_name = :foundry_name
            ORDER BY COALESCE(t.resolved_time, t.date_created) DESC
        """), {'foundry_name': foundry_name}).fetchall()

    # Return the dashboard template, passing foundry name and tickets
    return render_template("dashboard.html",
                           current_user={"username": username},
                           tickets=tickets,
                           foundry=foundry_name)

@app.route('/api/assign-to', methods=['GET'])
def get_assignees():
    """Fetch all assignees from the database including admin status."""
    try:
        result = db.session.execute(text("SELECT id, name, email, is_admin FROM assign_to ORDER BY name ASC")).fetchall()
        return jsonify([
            {"id": row[0], "name": row[1], "email": row[2], "is_admin": bool(row[3])} for row in result
        ])
    except Exception as e:
        return jsonify({'error': str(e)}), 500
  
@app.route('/api/assignable-users', methods=['GET'])
def get_assignable_users():
    """Fetch assignable users from the database."""
    try:
        query = text("SELECT id, name, email FROM assign_to ORDER BY name ASC")
        users = db.session.execute(query).fetchall()

        return jsonify([
            {"id": row[0], "name": row[1], "email": row[2]} for row in users
        ])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/tickets/<ticket_id>/assign', methods=['POST'])
def assign_ticket(ticket_id):
    """Assign a ticket to a user and update the database."""
    try:
        data = request.json
        assignee_id = data.get('assignee_id')
        comment = data.get('comment')

        if not assignee_id or not comment:
            return jsonify({'success': False, 'message': 'All fields are required.'}), 400

        # Fetch assignee details
        assignee = db.session.execute(
            text("SELECT name, email FROM assign_to WHERE id = :id"),
            {'id': assignee_id}
        ).fetchone()

        if not assignee:
            return jsonify({'success': False, 'message': 'Invalid assignee selected.'}), 400

        assignee_name, assignee_email = assignee

        # Update ticket assignment
        query = text("""
            UPDATE tickets
            SET assign_to = :name, assign_email = :email, comments = :comment
            WHERE ticket_id = :ticket_id
        """)
        db.session.execute(query, {
            'name': assignee_name,
            'email': assignee_email,
            'comment': comment,
            'ticket_id': ticket_id
        })
        db.session.commit()

        return jsonify({'success': True, 'message': 'Ticket assigned successfully!'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    
@app.route('/api/foundry/user/<foundry>/<username>', methods=['GET'])
def get_user_details(foundry, username):
    # Debugging line to check if the route is hit
    print(f"Fetching user for foundry: {foundry}, username: {username}")
    
    # Execute the database query to get the user details
    user = db.session.execute(
        text("SELECT name, email, phone FROM users WHERE foundry = :f AND LOWER(name) = LOWER(:u)"),
        {"f": foundry, "u": username}
    ).fetchone()

    # Check if the user exists in the database
    if user:
        # Return user details in JSON format
        return jsonify({
            "name": user.name,
            "email": user.email,
            "phone": user.phone
        })
    else:
        # Return error message if the user is not found
        return jsonify({"error": "User not found"}), 404
    
# Modify the get_user_details route to properly fetch by username
@app.route('/api/foundry/user/<username>', methods=['GET'])
def get_user_details_by_username(username):
    """Fetch user details by username from any foundry."""
    print(f"Fetching user details for username: {username}")
    
    # Execute query to find user by username in any foundry
    user = db.session.execute(
        text("""
            SELECT foundry_name, person_name, email, phone 
            FROM foundries 
            WHERE LOWER(user_name) = LOWER(:username) OR LOWER(person_name) = LOWER(:username)
        """),
        {"username": username}
    ).fetchone()

    # Check if the user exists in the database
    if user:
        # Return user details in JSON format
        return jsonify({
            "foundry": user[0],
            "name": user[1],
            "email": user[2],
            "phone": user[3]
        })
    else:
        # Return error message if the user is not found
        return jsonify({"error": "User not found"}), 404
    
# Assign To Management Page
@app.route('/assign-to')
def assign_to_page():
    return render_template('assign_to.html')

# Add New Assignee API
@app.route('/api/assign-to/add', methods=['POST'])
def add_assignee():
    """Add a new assignee to the database."""
    try:
        data = request.json
        name = data.get('name').strip()
        email = data.get('email').strip()

        if not name or not email:
            return jsonify({'error': 'Name and email are required'}), 400

        # Check if user already exists
        existing_user = db.session.execute(text("SELECT id FROM assign_to WHERE email = :email"), {"email": email}).fetchone()
        if existing_user:
            return jsonify({"error": "A user with this email already exists"}), 400

        # Insert new assignee
        query = text("INSERT INTO assign_to (name, email) VALUES (:name, :email)")
        db.session.execute(query, {'name': name, 'email': email})
        db.session.commit()

        return jsonify({'success': 'Assignee added successfully'})
    except Exception as e:
        return jsonify({'error': f'Failed to add assignee: {str(e)}'}), 500

# Edit Assignee API
@app.route('/api/assign-to/<int:assignee_id>', methods=['PUT'])
def edit_assignee(assignee_id):
    """Edit an existing assignee in the database."""
    try:
        data = request.json
        new_name = data.get('name').strip()
        new_email = data.get('email').strip()

        if not new_name or not new_email:
            return jsonify({'error': 'Name and email cannot be empty'}), 400

        query = text("UPDATE assign_to SET name = :name, email = :email WHERE id = :id")
        db.session.execute(query, {'name': new_name, 'email': new_email, 'id': assignee_id})
        db.session.commit()

        return jsonify({'success': 'Assignee updated successfully'})
    except Exception as e:
        return jsonify({'error': f'Failed to update assignee: {str(e)}'}), 500

# Delete Assignee API
@app.route('/api/assign-to/<int:assignee_id>', methods=['DELETE'])
def delete_assignee(assignee_id):
    """Delete an assignee from the database."""
    try:
        db.session.execute(text("DELETE FROM assign_to WHERE id = :id"), {'id': assignee_id})
        db.session.commit()
        return jsonify({'success': 'Assignee deleted successfully'})
    except Exception as e:
        return jsonify({'error': f'Failed to delete assignee: {str(e)}'}), 500

@app.route('/api/assign-to/admin/<int:assignee_id>', methods=['PUT'])
def update_admin_status(assignee_id):
    """Update admin status dynamically and send notification if assigned as admin."""
    try:
        data = request.json
        is_admin = data.get('is_admin')

        if is_admin is None:
            return jsonify({'success': False, 'message': 'is_admin field is required'}), 400

        # Update admin status in the database
        query = text("UPDATE assign_to SET is_admin = :is_admin WHERE id = :id")
        db.session.execute(query, {'is_admin': is_admin, 'id': assignee_id})
        db.session.commit()

        # Send email if assigned as admin
        if is_admin:
            assignee = db.session.execute(
                text("SELECT name, email FROM assign_to WHERE id = :id"), {'id': assignee_id}
            ).fetchone()

            if assignee:
                msg = Message(
                    subject="You have been assigned as an Admin",
                    recipients=[assignee.email],
                    body=f"Hello {assignee.name},\n\nYou have been assigned as an Admin for the Support Hub.\n\nBest,\nSupport Team"
                )
                threading.Thread(target=send_email_async, args=(app, msg)).start()

        return jsonify({'success': True, 'message': f'Admin status updated for ID {assignee_id}'}), 200

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/foundries', methods=['GET'])
def get_foundries():
    """Fetch all foundry names."""
    query = text("SELECT DISTINCT foundry_name FROM foundries")
    result = db.session.execute(query).fetchall()
    foundries = [row[0] for row in result]
    return jsonify(foundries)


@app.route('/new_ticket')
def new_ticket():
    """
    Render the Create Ticket form with appropriate options based on user role.
    """
    try:
        # Get URL parameters
        foundry = request.args.get('foundry') or request.args.get('foundry_name')  # Accept both parameter names
        username = request.args.get('user') or request.args.get('username')
        
        if not username:
            return "Missing username parameter", 400
            
        if not foundry:
            return "Foundry not specified", 400

        # Query for available case types for the ticket
        case_types = db.session.execute(text("SELECT id, name FROM case_types ORDER BY id")).fetchall()
        
        # Initialize user_info to None
        user_info = None
        
        # Check if user is super admin
        is_super_admin = "admin" in username.lower()
        
        # Get all foundries for dropdown (or just the selected one for foundry admin)
        if is_super_admin and not foundry:
            # Super admin without foundry selected - show all foundries
            foundries = [row[0] for row in db.session.execute(
                text("SELECT DISTINCT foundry_name FROM foundries ORDER BY foundry_name")
            ).fetchall()]
            
            # Allow super admin to choose foundry (don't show specific user info yet)
            return render_template('create_ticket.html',
                                  foundries=foundries,
                                  case_types=case_types,
                                  user_info=None,
                                  current_user={"username": username, "is_admin": True},
                                  show_foundry_dropdown=True)
        
        # If we have a foundry (either because user is foundry admin or super admin selected one)
        if foundry:
            # Try to find user info (if a regular user is creating ticket)
            if not is_super_admin:
                person = db.session.execute(text("""
                    SELECT person_name, email, phone FROM foundries 
                    WHERE foundry_name = :foundry AND LOWER(person_name) = LOWER(:username)
                """), {'foundry': foundry, 'username': username}).fetchone()
                
                if person:
                    user_info = {
                        'name': person[0],
                        'email': person[1],
                        'phone': person[2],
                        'foundry': foundry
                    }
            
            # Get all foundries for super admin, or just this one for foundry admin
            foundries = [foundry] if not is_super_admin else [row[0] for row in db.session.execute(
                text("SELECT DISTINCT foundry_name FROM foundries ORDER BY foundry_name")
            ).fetchall()]
            
            return render_template('create_ticket.html',
                                  foundries=foundries,
                                  case_types=case_types,
                                  user_info=user_info,
                                  current_user={"username": username, "is_admin": is_super_admin},
                                  show_foundry_dropdown=is_super_admin,
                                  selected_foundry=foundry)
        else:
            return "Foundry not specified", 400

    except Exception as e:
        # Handle unexpected errors gracefully
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500
      
@app.route('/tickets')
def tickets():
    """Render the tickets page with a dropdown to filter by foundry."""
    query = text("SELECT DISTINCT foundry_name FROM foundries")
    foundries = db.session.execute(query).fetchall()
    return render_template('tickets.html', foundries=[row[0] for row in foundries])

@app.route('/api/foundry/details/<foundry_name>', methods=['GET'])
def get_foundry_details(foundry_name):
    """Fetch details for a specific foundry, including names, emails, phones, and case types."""
    try:
        # Fetch users for the foundry
        user_query = text("""
            SELECT id, person_name, email, phone 
            FROM foundries 
            WHERE foundry_name = :foundry_name
        """)
        users = db.session.execute(user_query, {'foundry_name': foundry_name}).fetchall()

        # Fetch case types
        case_type_query = text("SELECT name FROM case_types ORDER BY id")
        case_types = db.session.execute(case_type_query).fetchall()

        if not users:
            return jsonify({'error': 'No users found for this foundry'}), 404

        return jsonify({
            'names': [{'id': row[0], 'name': row[1], 'email': row[2], 'phone': row[3]} for row in users], 
            'case_types': [row[0] for row in case_types]
        })

    except Exception as e:
        return jsonify({'error': f'Failed to fetch foundry details: {str(e)}'}), 500

@app.route('/user-management')
def user_management():
    """Render the User Management page with the foundry selection dropdown."""
    return render_template('user_management.html')

@app.route('/api/foundry/add', methods=['POST'])
def add_foundry():
    """Add a new foundry with multiple users."""
    try:
        data = request.json
        foundry_name = data.get('foundry_name')
        users = data.get('users')

        if not foundry_name or not users or len(users) == 0:
            return jsonify({'error': 'Foundry name and at least one user are required'}), 400

        # Insert users for this foundry
        for user in users:
            db.session.execute(
                text("""
                    INSERT INTO foundries (foundry_name, person_name, email, phone) 
                    VALUES (:foundry_name, :name, :email, :phone)
                """),
                {
                    'foundry_name': foundry_name, 
                    'name': user['name'], 
                    'email': user['email'], 
                    'phone': user['phone'] if user['phone'] else None  # ✅ Store NULL if empty
                }
            )

        db.session.commit()
        return jsonify({'success': 'Foundry and users added successfully'})

    except Exception as e:
        return jsonify({'error': f'Failed to add foundry: {str(e)}'}), 500

        # Insert users for this foundry
        for user in users:
            db.session.execute(
                text("INSERT INTO foundries (foundry_name, person_name, email, phone) VALUES (:foundry_name, :name, :email, :phone)"),
                {'foundry_name': foundry_name, 'name': user['name'], 'email': user['email'], 'phone': user['phone']}
            )

        db.session.commit()
        return jsonify({'success': 'Foundry and users added successfully'})

    except Exception as e:
        return jsonify({'error': f'Failed to add foundry: {str(e)}'}), 500

@app.route('/api/foundry/user/add', methods=['POST'])
def add_user_to_foundry():
    """Add a new user to an existing foundry."""
    try:
        data = request.json
        foundry_name = data.get('foundry')
        name = data.get('name')
        email = data.get('email')
        phone = data.get('phone') if data.get('phone') else None  # ✅ Store NULL if empty

        if not foundry_name or not name or not email:
            return jsonify({'error': 'Foundry name, name, and email are required'}), 400

        # Insert user into foundry
        db.session.execute(
            text("""
                INSERT INTO foundries (foundry_name, person_name, email, phone) 
                VALUES (:foundry_name, :name, :email, :phone)
            """),
            {'foundry_name': foundry_name, 'name': name, 'email': email, 'phone': phone}
        )

        db.session.commit()
        return jsonify({'success': 'User added successfully'})

    except Exception as e:
        return jsonify({'error': f'Failed to add user: {str(e)}'}), 500

@app.route('/api/foundry/user/edit/<int:user_id>', methods=['PUT'])
def edit_foundry_user(user_id):
    """Edit an existing foundry user."""
    try:
        data = request.json
        query = text("UPDATE foundries SET person_name = :name, email = :email, phone = :phone WHERE id = :user_id")
        db.session.execute(query, {**data, 'user_id': user_id})
        db.session.commit()
        return jsonify({'success': 'User updated successfully'})

    except Exception as e:
        return jsonify({'error': f'Failed to update user: {str(e)}'}), 500

@app.route('/api/foundry/user/delete/<int:user_id>', methods=['DELETE'])
def delete_foundry_user(user_id):
    """Delete a foundry user."""
    try:
        # Delete the user based on their ID
        db.session.execute(text("DELETE FROM foundries WHERE id = :user_id"), {'user_id': user_id})
        db.session.commit()

        return jsonify({'success': 'User deleted successfully'})

    except Exception as e:
        return jsonify({'error': f'Failed to delete user: {str(e)}'}), 500

from flask import request, jsonify
from werkzeug.utils import secure_filename
from sqlalchemy import text
import os, random

@app.route('/create_ticket', methods=['GET', 'POST'])
def create_ticket():
    """Handle ticket creation with proper form handling and database interaction."""
    if request.method == 'GET':
        # Get URL parameters
        foundry = request.args.get('foundry')
        username = request.args.get('user')

        if not foundry:
            return "Missing foundry parameter", 400

        # Get case types for the form
        case_types = db.session.execute(text("SELECT id, name FROM case_types ORDER BY id")).fetchall()
        
        # Initialize user_info
        user_info = None
        
        # If username is provided, try to find matching user in the foundry
        if username:
            user_query = text("""
                SELECT person_name, email, phone FROM foundries 
                WHERE foundry_name = :foundry_name AND person_name = :username
            """)
            
            user = db.session.execute(user_query, 
                                     {'foundry_name': foundry, 'username': username}).fetchone()
            
            if user:
                user_info = {
                    'name': user[0],
                    'email': user[1],
                    'phone': user[2],
                    'foundry': foundry
                }

        # Get all foundries for the dropdown (limited to the current foundry in this case)
        foundries = [foundry]
        
        return render_template('create_ticket.html',
                              foundries=foundries,
                              case_types=case_types,
                              user_info=user_info,
                              current_user={"username": username})
    
    elif request.method == 'POST':
        try:
            # Extract form data
            foundry_name = request.form.get('foundry_name')
            name = request.form.get('name')
            email = request.form.get('email')
            phone = request.form.get('phone', '')  # We'll still get this but won't use it in SQL
            case_type_id = request.form.get('helpTopic')
            issue_description = request.form.get('issueDescription')
            priority = request.form.get('priorityLevel')
            
            # Validate required fields
            if not all([foundry_name, name, email, case_type_id, issue_description, priority]):
                return jsonify({"success": False, "message": "Missing required fields"}), 400
            
            # Get case type name from ID
            case_type_query = text("SELECT name FROM case_types WHERE id = :id")
            case_type_result = db.session.execute(case_type_query, {'id': case_type_id}).fetchone()
            if not case_type_result:
                return jsonify({"success": False, "message": "Invalid case type"}), 400
            
            case_type_name = case_type_result[0]
            
            # Generate ticket ID (use a more robust method in production)
            current_time = datetime.now()
            ticket_id = f"{foundry_name[:3].upper()}-{current_time.strftime('%Y%m%d')}-{random.randint(1000, 9999)}"
            
            # Handle file upload if provided
            attachment_filename = None
            if 'attachment_file' in request.files:
                file = request.files['attachment_file']
                if file and file.filename and allowed_file(file.filename):
                    # Create a safe filename with user and timestamp
                    filename = secure_filename(file.filename)
                    base, ext = os.path.splitext(filename)
                    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                    new_filename = f"{name.replace(' ', '_')}_{timestamp}{ext}"
                    
                    # Save to user files folder
                    file_path = os.path.join(USER_FILES_FOLDER, new_filename)
                    file.save(file_path)
                    attachment_filename = new_filename
            
            # FIXED: Remove 'phone' from the SQL insert query
            insert_query = text("""
                INSERT INTO tickets 
                (ticket_id, foundry_name, name, email, issue, issue_description, 
                priority, status, attachment_file, date_created, last_updated)
                VALUES 
                (:ticket_id, :foundry_name, :name, :email, :issue, :issue_description,
                :priority, 'Open', :attachment_file, NOW(), NOW())
            """)
            
            # FIXED: Remove 'phone' parameter from the execution
            db.session.execute(insert_query, {
                'ticket_id': ticket_id,
                'foundry_name': foundry_name,
                'name': name,
                'email': email,
                'issue': case_type_name,
                'issue_description': issue_description,
                'priority': priority,
                'attachment_file': attachment_filename
            })
            
            db.session.commit()
            
            # Send confirmation email
            send_ticket_confirmation_email(
                foundry_name, email, ticket_id, name, 
                case_type_name, issue_description, priority
            )
            
            # Return success response
            return jsonify({
                "success": True,
                "message": "Ticket created successfully",
                "ticket_id": ticket_id
            })
            
        except Exception as e:
            db.session.rollback()
            print(f"Error creating ticket: {str(e)}")
            return jsonify({"success": False, "message": f"Error creating ticket: {str(e)}"}), 500
             
def send_ticket_confirmation_email(foundry, user_email, ticket_id, user_name, issue, description, priority):
    try:
        admin_results = db.session.execute(text("SELECT email FROM assign_to WHERE is_admin = TRUE")).fetchall()
        admin_emails = [row[0] for row in admin_results]

        if not admin_emails:
            admin_emails = ["muralidharan@mpminfosoft.com"]  # Default admin email

        # Email for the User
        user_msg = Message(
            subject=f"Ticket {foundry} - #{ticket_id} Created Successfully",
            recipients=[user_email],
            body=f"""
            Hello {user_name},

            Your support ticket has been successfully created.

            Ticket ID: {ticket_id}
            Issue: {issue}
            Priority: {priority}
            Description: {description}

            Thank you,
            Support Hub Team
            """
        )

        # Email for the Admins
        admin_msg = Message(
            subject=f"New Ticket {foundry} - #{ticket_id} Created",
            recipients=admin_emails,
            body=f"""
            Hello Admin,

            A new support ticket has been created.

            Ticket ID: {ticket_id}
            User: {user_name}
            Email: {user_email}
            Issue: {issue}
            Priority: {priority}
            Description: {description}
     
            Thank you,
            Support System
            """
        ) 

        # Run email sending in background threads
        threading.Thread(target=send_email_async, args=(app, user_msg)).start()
        threading.Thread(target=send_email_async, args=(app, admin_msg)).start()

    except Exception as e:
        print(f"Error sending confirmation email: {e}")
        
@app.route('/api/tickets', methods=['GET'])
def get_tickets():
    """Fetch all tickets or filter by foundry, ordered by last_updated."""
    foundry_name = request.args.get('foundry_name')
    query = """
        SELECT ticket_id, foundry_name, name, email, priority, issue, issue_description, status, comments, resolved_file, date_created, last_updated
        FROM tickets
    """
    if foundry_name:
        query += " WHERE foundry_name = :foundry_name ORDER BY last_updated DESC"
        result = db.session.execute(text(query), {'foundry_name': foundry_name}).fetchall()
    else:
        query += " ORDER BY last_updated DESC"
        result = db.session.execute(text(query)).fetchall()

    tickets = [
        {
            'ticket_id': row[0],
            'foundry_name': row[1],
            'name': row[2],
            'email': row[3],
            'priority': row[4],
            'issue': row[5],
            'description': row[6],
            'status': row[7],
            'comments': row[8],
            'resolved_file': row[9],
            'date_created': row[10].strftime('%Y-%m-%d %H:%M:%S'),
            'last_updated': row[11].strftime('%Y-%m-%d %H:%M:%S'),
        }
        for row in result
    ]
    return jsonify(tickets)


@app.route('/api/tickets/<ticket_id>/file', methods=['GET'])
def get_ticket_file(ticket_id):
    """
    Serve the resolved file for a specific ticket based on ticket ID.
    """
    query = text("SELECT resolved_file FROM tickets WHERE ticket_id = :ticket_id")
    result = db.session.execute(query, {'ticket_id': ticket_id}).fetchone()

    if not result or not result[0]:
        return jsonify({'error': 'No file associated with this ticket'}), 404

    file_path = result[0]
    filename = os.path.basename(file_path)

    try:
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=True)
    except FileNotFoundError:
        return jsonify({'error': 'File not found on server'}), 404


@app.route('/comments/<ticket_id>', methods=['GET'])
def get_comments(ticket_id):
    """Fetch comments for a specific ticket."""
    query = text("""
        SELECT comments
        FROM tickets
        WHERE ticket_id = :ticket_id
    """)
    result = db.session.execute(query, {'ticket_id': ticket_id}).fetchone()

    if result is None:
        return jsonify({'comments': 'No comments available.'})

    return jsonify({'comments': result[0]})

@app.route('/api/tickets/<ticket_id>/close', methods=['POST'])
def close_ticket_with_file(ticket_id):
    """
    Close a ticket with an optional file and comment.
    """
    file = request.files.get('file')
    comment = request.form.get('comment', '')

    resolved_file_path = None
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        resolved_file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(resolved_file_path)

    query = text("""
        UPDATE tickets
        SET status = 'Closed',
            comments = :comment,
            resolved_file = :resolved_file,
            resolved_time = NOW()
        WHERE ticket_id = :ticket_id
    """)
    db.session.execute(query, {
        'comment': comment,
        'resolved_file': resolved_file_path,
        'ticket_id': ticket_id,
    })
    db.session.commit()

    return jsonify({'success': True, 'message': 'Ticket closed successfully!'})


@app.route('/api/tickets/<ticket_id>', methods=['PUT'])
def update_ticket_status(ticket_id):
    """
    Update the status of a ticket.
    """
    data = request.json
    new_status = data.get('status', '')

    if not new_status:
        return jsonify({'success': False, 'message': 'Status is required'}), 400

    query = text("""
        UPDATE tickets
        SET status = :status,
            resolved_time = CASE WHEN :status = 'Closed' THEN NOW() ELSE resolved_time END
        WHERE ticket_id = :ticket_id
    """)
    db.session.execute(query, {'status': new_status, 'ticket_id': ticket_id})
    db.session.commit()

    return jsonify({'success': True, 'message': f'Status updated to {new_status} successfully!'})

@app.route('/api/chart-data')
def chart_data():
    """Fetch ticket data and return JSON for charts."""
    foundry_name = request.args.get('foundry_name')

    if foundry_name:
        # Filter by foundry
        case_type_query = text("SELECT issue, COUNT(*) FROM tickets WHERE foundry_name = :foundry GROUP BY issue")
        case_type_results = db.session.execute(case_type_query, {'foundry': foundry_name}).fetchall()
        priority_query = text("SELECT priority, COUNT(*) FROM tickets WHERE foundry_name = :foundry GROUP BY priority")
        priority_results = db.session.execute(priority_query, {'foundry': foundry_name}).fetchall()
    else:
        case_type_query = text("SELECT issue, COUNT(*) FROM tickets GROUP BY issue")
        case_type_results = db.session.execute(case_type_query).fetchall()
        priority_query = text("SELECT priority, COUNT(*) FROM tickets GROUP BY priority")
        priority_results = db.session.execute(priority_query).fetchall()

    case_type_data = [[row[0], row[1]] for row in case_type_results]
    priority_data = [[row[0], row[1]] for row in priority_results]

    return jsonify({
        "case_types": case_type_data,
        "priority": priority_data
    })


@app.route('/case-types')
def case_types():
    """Render the Case Types page."""
    return render_template('case_types.html')

@app.route('/api/case-types/add', methods=['POST'])
def add_case_type():
    data = request.json
    case_type_name = data.get('name').strip()

    # Prevent duplicate entries
    existing = db.session.execute(text("SELECT COUNT(*) FROM case_types WHERE name = :name"), {'name': case_type_name}).scalar()
    if existing > 0:
        return jsonify({'error': 'Case type already exists'}), 400

    # Insert into database
    db.session.execute(text("INSERT INTO case_types (name) VALUES (:name)"), {'name': case_type_name})
    db.session.commit()
    return jsonify({'success': 'Case type added successfully'})

@app.route('/api/case-types/<int:case_type_id>', methods=['PUT'])
def edit_case_type(case_type_id):
    data = request.json
    new_name = data.get('name').strip()

    # Check if new name already exists
    existing = db.session.execute(text("SELECT COUNT(*) FROM case_types WHERE name = :name"), {'name': new_name}).scalar()
    if existing > 0:
        return jsonify({'error': 'Case type already exists'}), 400

    # Update in database
    db.session.execute(text("UPDATE case_types SET name = :name WHERE id = :case_type_id"), {'name': new_name, 'case_type_id': case_type_id})
    db.session.commit()
    return jsonify({'success': 'Case type updated successfully'})

@app.route('/api/case-types/<int:case_type_id>', methods=['DELETE'])
def delete_case_type(case_type_id):
    db.session.execute(text("DELETE FROM case_types WHERE id = :case_type_id"), {'case_type_id': case_type_id})
    db.session.commit()
    return jsonify({'success': 'Case type deleted successfully'})

@app.route('/api/case-types', methods=['GET'])
def get_case_types():
    """Fetch all case types from the database."""
    try:
        query = text("SELECT id, name FROM case_types ORDER BY id")
        result = db.session.execute(query).fetchall()
        
        if not result:
            return jsonify({'message': 'No case types found'}), 404
        
        case_types = [{"id": row[0], "name": row[1]} for row in result]
        return jsonify(case_types)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True)
