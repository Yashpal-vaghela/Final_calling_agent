let coveredCities = [];

async function loadCities() {
    try {
        const response = await fetch('/api/covered-cities');
        if (!response.ok) {
            throw new Error('Network response was not ok');
        }
        coveredCities = await response.json();
        
        const datalist = document.getElementById('cities-datalist');
        if (datalist) {
            coveredCities.forEach(city => {
                const option = document.createElement('option');
                option.value = city;
                datalist.appendChild(option);
            });
        }
    } catch (error) {
        console.error('Error loading cities:', error);
        // If loading fails, we log an error. The form validation will still check against coveredCities (which will be empty)
        // preventing submission if the list fails to load.
        showStatus('error', 'Configuration Error', 'Unable to load the list of covered cities. Please refresh the page.');
        const submitBtn = document.getElementById('submitBtn');
        if (submitBtn) {
            submitBtn.disabled = true;
        }
    }
}

function validateCity(cityName) {
    if (!cityName) return false;
    const lowerCityName = cityName.trim().toLowerCase();
    return coveredCities.some(city => city.toLowerCase() === lowerCityName);
}

document.addEventListener('DOMContentLoaded', loadCities);
