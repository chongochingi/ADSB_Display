const API_URL = '/api/data';
let aircraftData = [];


// DOM Elements
const aircraftList = document.getElementById('aircraft-list');
const totalCountEl = document.getElementById('total-count');
const seenCountEl = document.getElementById('seen-count');
const typeCountEl = document.getElementById('type-count');
const closestDistEl = document.getElementById('closest-dist');
const closestTypeEl = document.getElementById('closest-type');
const furthestDistEl = document.getElementById('furthest-dist');
const furthestTypeEl = document.getElementById('furthest-type');
const highestAltEl = document.getElementById('highest-alt');
const highestTypeEl = document.getElementById('highest-type');
const lowestAltEl = document.getElementById('lowest-alt');
const lowestTypeEl = document.getElementById('lowest-type');


// Fetch data from backend
async function fetchData() {
    try {
        const response = await fetch(API_URL);
        const data = await response.json();

        if (data.aircraft) {
            aircraftData = data.aircraft;
            // Pass full data object to updateStats to access 'stats'
            updateStats(data.stats);
            renderAircraft();
        }
    } catch (error) {
        console.error('Error fetching data:', error);
    }
}

// Update Stats
function updateStats(stats) {
    try {
        if (totalCountEl) totalCountEl.textContent = aircraftData.length;
        if (seenCountEl && stats && stats.total_seen) seenCountEl.textContent = stats.total_seen;
        if (typeCountEl && stats && stats.unique_types) typeCountEl.textContent = stats.unique_types;

        // Find closest and furthest aircraft (if 'r_dst' is available)
        // Note: r_dst is radial distance in nm from receiver
        const withDist = aircraftData.filter(a => a.r_dst !== undefined);

        if (withDist.length > 0) {
            // Sort by distance
            withDist.sort((a, b) => a.r_dst - b.r_dst);

            const closest = withDist[0];
            const furthest = withDist[withDist.length - 1];

            // Update Closest
            if (closestDistEl) closestDistEl.textContent = `${closest.r_dst.toFixed(1)} nm`;
            if (closestTypeEl) closestTypeEl.textContent = closest.desc || closest.t || closest.category || 'Unknown';

            // Update Furthest
            if (furthestDistEl) furthestDistEl.textContent = `${furthest.r_dst.toFixed(1)} nm`;
            if (furthestTypeEl) furthestTypeEl.textContent = furthest.desc || furthest.t || furthest.category || 'Unknown';
        } else {
            if (closestDistEl) closestDistEl.textContent = '--';
            if (closestTypeEl) closestTypeEl.textContent = '--';
            if (furthestDistEl) furthestDistEl.textContent = '--';
            if (furthestTypeEl) furthestTypeEl.textContent = '--';
        }

        // Find Highest and Lowest Altitude
        // Filter for valid numeric altitude (exclude 'ground' or undefined)
        const withAlt = aircraftData.filter(a => typeof a.alt_baro === 'number');

        if (withAlt.length > 0) {
            withAlt.sort((a, b) => a.alt_baro - b.alt_baro);

            const lowest = withAlt[0];
            const highest = withAlt[withAlt.length - 1];

            if (lowestAltEl) lowestAltEl.textContent = `${lowest.alt_baro} ft`;
            if (lowestTypeEl) lowestTypeEl.textContent = lowest.desc || lowest.t || lowest.category || 'Unknown';

            if (highestAltEl) highestAltEl.textContent = `${highest.alt_baro} ft`;
            if (highestTypeEl) highestTypeEl.textContent = highest.desc || highest.t || highest.category || 'Unknown';
        } else {
            if (lowestAltEl) lowestAltEl.textContent = '--';
            if (lowestTypeEl) lowestTypeEl.textContent = '--';
            if (highestAltEl) highestAltEl.textContent = '--';
            if (highestTypeEl) highestTypeEl.textContent = '--';
        }

    } catch (e) {
        console.error("Error updating stats:", e);
    }
}

// Filter Logic
// Filter Logic - Removed (User requested no tabs)
function getFilteredData() {
    return aircraftData;
}

// Helper to convert degrees to cardinal direction
function getCardinalDirection(angle) {
    if (typeof angle !== 'number') return '';
    const directions = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
    const index = Math.round(((angle %= 360) < 0 ? angle + 360 : angle) / 45) % 8;
    return directions[index];
}

let lastSortTime = 0;

// Render Aircraft Cards (Smart Update)
function renderAircraft() {
    const filtered = getFilteredData();
    const now = Date.now();
    // Sort every 60 seconds (60000ms)
    // Also sort if it's the first run (lastSortTime === 0)
    const shouldSort = (now - lastSortTime) > 60000 || lastSortTime === 0;

    if (shouldSort) {
        // Sort by distance (r_dst) ascending
        filtered.sort((a, b) => {
            const distA = a.r_dst !== undefined ? a.r_dst : 99999;
            const distB = b.r_dst !== undefined ? b.r_dst : 99999;
            return distA - distB;
        });
        lastSortTime = now;
    } else {
        // Maintain current DOM order for stability
        // 1. Map existing hex IDs to their DOM index
        const domOrder = Array.from(aircraftList.children).map(node => node.id);
        const domIndexMap = new Map(domOrder.map((id, index) => [id, index]));

        // 2. Sort filtered list to match DOM order
        // New aircraft (not in DOM) get index 99999 and go to the end
        filtered.sort((a, b) => {
            const idxA = domIndexMap.has(a.hex) ? domIndexMap.get(a.hex) : 99999;
            const idxB = domIndexMap.has(b.hex) ? domIndexMap.get(b.hex) : 99999;
            return idxA - idxB;
        });
    }

    const currentHexes = new Set(filtered.map(ac => ac.hex));

    // 1. Remove aircraft that are no longer in the list
    Array.from(aircraftList.children).forEach(card => {
        if (!currentHexes.has(card.id)) {
            card.remove();
        }
    });

    // 2. Add or Update aircraft
    filtered.forEach((ac, index) => {
        let card = document.getElementById(ac.hex);
        const flight = ac.flight ? ac.flight.trim() : (ac.r || 'N/A');
        const icao = ac.hex.toUpperCase();
        const alt = ac.alt_baro !== undefined ? ac.alt_baro : 'Ground';
        const speed = ac.gs !== undefined ? Math.round(ac.gs) : '--';
        const dist = ac.r_dst !== undefined ? ac.r_dst.toFixed(1) : '--';

        // Calculate Direction from User
        const direction = ac.r_dir !== undefined ? getCardinalDirection(ac.r_dir) : '';
        const distDisplay = direction ? `${dist} nm ${direction}` : `${dist} nm`;
        const type = ac.t || ac.category || '--';
        const desc = ac.desc || '';
        const isMilitary = (ac.dbFlags & 1) || (ac.mil === true);
        const isNewType = ac.is_new_type === true;
        const isOU = flight.startsWith('OU');

        // Prepare Wiki URL
        const wikiQuery = ac.full_desc || ac.desc || ac.t || '';
        const wikiUrl = `https://en.wikipedia.org/w/index.php?search=${encodeURIComponent(wikiQuery)}`;


        if (!card) {
            // Create new card
            card = document.createElement('div');
            card.id = ac.hex;
            // Add classes dynamically
            let classes = ['aircraft-card'];
            if (isMilitary) classes.push('military');
            if (isOU) classes.push('ou');

            card.className = classes.join(' ');

            // Add click handler for Wikipedia
            card.onclick = (e) => {
                if (e.target.tagName !== 'BUTTON') {
                    window.open(wikiUrl, '_blank');
                }
            };

            card.innerHTML = `
                <div class="card-header">
                    <div>
                        <span class="flight-id">${flight}</span> ${isNewType ? '<span class="new-type-star" title="First time seeing this type!">⭐</span>' : ''}
                        <div class="desc">${desc}</div>
                    </div>
                </div>
                <div class="card-body">
                    <div class="data-row">
                        <span class="data-label">ALTITUDE</span>
                        <span class="data-val" data-field="alt">${alt} ft</span>
                    </div>
                    <div class="data-row">
                        <span class="data-label">SPEED</span>
                        <span class="data-val" data-field="speed">${speed} kts</span>
                    </div>
                    <div class="data-row">
                        <span class="data-label">DISTANCE</span>
                        <span class="data-val" data-field="dist">${distDisplay}</span>
                    </div>
                     <div class="data-row">
                        <span class="data-label">TYPE</span>
                        <span class="data-val" data-field="type">${type}</span>
                    </div>
                </div>
            `;
            // Append new cards to the end (or correct position)
            // If we are at the end of the list, append.
            if (index >= aircraftList.children.length) {
                aircraftList.appendChild(card);
            } else {
                aircraftList.insertBefore(card, aircraftList.children[index]);
            }
        } else {
            // Update existing card
            const flightEl = card.querySelector('.flight-id');
            if (flightEl) flightEl.textContent = flight;

            const altEl = card.querySelector('[data-field="alt"]');
            if (altEl) altEl.textContent = `${alt} ft`;

            const speedEl = card.querySelector('[data-field="speed"]');
            if (speedEl) speedEl.textContent = `${speed} kts`;

            const distEl = card.querySelector('[data-field="dist"]');
            if (distEl) distEl.textContent = distDisplay;

            const typeEl = card.querySelector('[data-field="type"]');
            if (typeEl) typeEl.textContent = type;

            // Toggle military class if changed (rare but possible)
            if (isMilitary) card.classList.add('military');
            else card.classList.remove('military');

            // Toggle OU class
            if (isOU) card.classList.add('ou');
            else card.classList.remove('ou');

            // Anti-Flicker: Only move DOM node if position changed
            const currentAtIndex = aircraftList.children[index];
            if (currentAtIndex !== card) {
                if (currentAtIndex) {
                    aircraftList.insertBefore(card, currentAtIndex);
                } else {
                    aircraftList.appendChild(card);
                }
            }

            // Update onclick handler with potentially new data
            card.onclick = (e) => {
                if (e.target.tagName !== 'BUTTON') {
                    window.open(wikiUrl, '_blank');
                }
            };
        }
    });
}


// Event Listeners for Filters


// Close Proximity Logic
let closeAircraft = [];
let closeIndex = 0;
const alertContainer = document.getElementById('close-proximity-container');
const alertContent = document.getElementById('alert-content');

function updateCloseProximityDisplay() {
    // 1. Identify Close Aircraft (< 4nm)
    // Note: r_dst is radial distance.
    closeAircraft = aircraftData.filter(ac => ac.r_dst !== undefined && ac.r_dst < 4);

    if (closeAircraft.length === 0) {
        if (alertContainer) alertContainer.classList.add('hidden');
        return;
    }

    if (alertContainer) alertContainer.classList.remove('hidden');

    // Ensure index is valid
    if (closeIndex >= closeAircraft.length) {
        closeIndex = 0;
    }

    const ac = closeAircraft[closeIndex];
    if (ac && alertContent) {
        const flight = ac.flight ? ac.flight.trim() : 'N/A';
        const alt = ac.alt_baro !== undefined ? ac.alt_baro : 'Ground';
        const dist = ac.r_dst.toFixed(1);
        const type = ac.t || ac.category || 'Unknown';
        const desc = ac.desc || '';

        alertContent.innerHTML = `
            <div>${flight}</div>
            <div class="alert-detail" style="font-weight: bold; color: #fff;">${type} ${desc}</div>
            <div class="alert-detail">${dist} nm away • ${alt} ft</div>
        `;
    }
}

// Rotate through close aircraft
setInterval(() => {
    if (closeAircraft.length > 1) {
        closeIndex = (closeIndex + 1) % closeAircraft.length;
        updateCloseProximityDisplay(); // Force update immediately on rotation
    }
}, 3000);

// Start polling
fetchData();
setInterval(fetchData, 1000);
// Update display on fetch as well to catch new data/removals
setInterval(updateCloseProximityDisplay, 1000);
