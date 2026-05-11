from flask import Flask, render_template, jsonify
from data_source import get_aircraft_data, get_unique_types_data, get_flight_schedule
import datetime

app = Flask(__name__)

# Custom filter for formatting timestamps
@app.template_filter('datetime')
def format_datetime(value):
    if value:
        return datetime.datetime.fromtimestamp(value).strftime('%Y-%m-%d %H:%M:%S')
    return ""

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/unique_types')
def unique_types():
    types_data = get_unique_types_data()
    return render_template('unique_types.html', types=types_data)

@app.route('/schedule')
def schedule():
    schedule_data = get_flight_schedule()
    return render_template('schedule.html', schedule=schedule_data)

@app.route('/routes_board')
def routes_board():
    return render_template('routes_board.html')

@app.route('/map')
def live_map():
    return render_template('map.html')

@app.route('/api/data')
def api_data():
    data = get_aircraft_data()
    if data:
        return jsonify(data)
    else:
        return jsonify({"error": "Failed to fetch data", "aircraft": []}), 500

if __name__ == '__main__':
    # Running on port 5001 to avoid conflict with Pi-hole on 80/admin or other services
    app.run(debug=True, host='0.0.0.0', port=5001, ssl_context='adhoc')
