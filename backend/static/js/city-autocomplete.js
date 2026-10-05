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
            datalist.innerHTML = '';
            coveredCities.forEach(city => {
                const option = document.createElement('option');
                option.value = city;
                datalist.appendChild(option);
            });
        }
    } catch (error) {
        console.warn('Covered cities datalist load issue:', error);
    }
}

function initPincodeCitySync() {
    const pincodeInput = document.getElementById('pincode');
    const cityInput = document.getElementById('city');
    if (!pincodeInput || !cityInput) return;

    let cityGroup = document.getElementById('cityGroup');
    if (!cityGroup) {
        cityGroup = cityInput.closest('.form-group') || cityInput.parentElement;
        if (cityGroup && !cityGroup.id) {
            cityGroup.id = 'cityGroup';
        }
    }

    // The City field is ALWAYS VISIBLE on all forms. Never hide it.
    if (cityGroup && cityGroup.style.display === 'none') {
        cityGroup.style.display = '';
    }

    let cityHint = document.getElementById('cityHint');
    if (!cityHint && cityGroup) {
        cityHint = document.createElement('div');
        cityHint.id = 'cityHint';
        cityHint.style.fontSize = '11.5px';
        cityHint.style.marginTop = '4px';
        cityHint.style.display = 'none';
        cityGroup.appendChild(cityHint);
    }

    let lookupTimeout = null;

    async function fetchCityFromPostalApi(pin) {
        if (!pin || pin.length !== 6) {
            if (cityHint) cityHint.style.display = 'none';
            return;
        }

        if (cityHint) {
            cityHint.style.display = 'block';
            cityHint.style.color = '#ECC85B';
            cityHint.innerHTML = '⏳ Fetching city from Postal API...';
        }

        try {
            const resp = await fetch(`/api/pincode-lookup/${pin}`);
            if (resp.ok) {
                const data = await resp.json();
                if (data.success && data.city) {
                    cityInput.value = data.city;
                    if (cityHint) {
                        cityHint.style.display = 'block';
                        cityHint.style.color = '#10B981';
                        cityHint.innerHTML = `📍 Auto-fetched from Postal API: <strong>${data.city}</strong> (you can also edit/select)`;
                    }
                    return;
                }
            }
            if (cityHint) {
                cityHint.style.display = 'block';
                cityHint.style.color = '#94A3B8';
                cityHint.innerHTML = 'ℹ️ Postal API: Please select city from list';
            }
        } catch (err) {
            if (cityHint) {
                cityHint.style.display = 'block';
                cityHint.style.color = '#94A3B8';
                cityHint.innerHTML = 'ℹ️ Please select or enter your city';
            }
        }
    }

    pincodeInput.addEventListener('input', () => {
        const raw = pincodeInput.value;
        const clean = raw.replace(/\D/g, '').slice(0, 6);
        if (pincodeInput.value !== clean) {
            pincodeInput.value = clean;
        }

        clearTimeout(lookupTimeout);
        if (clean.length === 6) {
            lookupTimeout = setTimeout(() => fetchCityFromPostalApi(clean), 100);
        } else {
            if (cityHint) cityHint.style.display = 'none';
        }
    });

    pincodeInput.addEventListener('paste', () => {
        setTimeout(() => {
            const clean = pincodeInput.value.replace(/\D/g, '').slice(0, 6);
            pincodeInput.value = clean;
            if (clean.length === 6) {
                fetchCityFromPostalApi(clean);
            }
        }, 30);
    });

    cityInput.addEventListener('input', () => {
        if (cityHint) {
            cityHint.style.display = 'block';
            cityHint.style.color = '#94A3B8';
            cityHint.innerHTML = '✏️ Custom city selected.';
        }
    });

    // If pincode is already filled on initial render (e.g. autofill or query params)
    const initialPin = pincodeInput.value ? pincodeInput.value.replace(/\D/g, '').slice(0, 6) : '';
    if (initialPin.length === 6 && !cityInput.value) {
        fetchCityFromPostalApi(initialPin);
    }
}

async function validateCityPincodeConsistency(cityName, pincode) {
    if (!pincode || pincode.length !== 6) {
        return { valid: false, message: 'Please enter a valid 6-digit area PIN code.' };
    }
    if (!cityName || !cityName.trim()) {
        return { valid: false, message: 'Please enter or select a city.' };
    }

    const cleanPin = pincode.trim();
    const cleanCity = cityName.trim();

    try {
        const res = await fetch('/api/validate-city-pincode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ city: cleanCity, pincode: cleanPin })
        });
        if (res.ok) {
            return await res.json();
        }
    } catch (e) {
        // If validation request fails to reach server, allow server-side submit endpoint to enforce
    }

    return { valid: true };
}

function validateCity(cityName) {
    if (!cityName) return false;
    const lowerCityName = cityName.trim().toLowerCase();
    return coveredCities.some(city => city.toLowerCase() === lowerCityName);
}

document.addEventListener('DOMContentLoaded', () => {
    loadCities();
    initPincodeCitySync();
});
