document.addEventListener('DOMContentLoaded', () => {
    loadFoundries();
    updateFoundryInfo();
    async function loadFoundries() {
        const foundrySelect = document.getElementById('foundry');
        try {
            const response = await fetch('/api/foundries');
            const data = await response.json();
    
            foundrySelect.innerHTML = '<option value="">-- Select --</option>';
            data.foundries.forEach(foundry => {
                const option = document.createElement('option');
                option.value = foundry;
                option.textContent = foundry;
                foundrySelect.appendChild(option);
            });
        } catch (error) {
            alert('Error loading foundries.');
            console.error('Error fetching foundries:', error);
        }
    }
    

    async function updateFoundryInfo() {
        const foundry = document.getElementById('foundry').value;
        const nameSelect = document.getElementById('foundryName');
        const emailSelect = document.getElementById('foundryEmail');
        const phoneSelect = document.getElementById('foundryPhone');
        nameSelect.innerHTML = '';
        emailSelect.innerHTML = '';
        phoneSelect.innerHTML = '';
        if (foundry) {
            try {
                const response = await fetch(`/api/foundry/${foundry}`);
                const data = await response.json();
                data.names.forEach(name => {
                    const option = document.createElement('option');
                    option.textContent = name;
                    nameSelect.appendChild(option);
                });
                data.emails.forEach(email => {
                    const option = document.createElement('option');
                    option.textContent = email;
                    emailSelect.appendChild(option);
                });
                data.phones.forEach(phone => {
                    const option = document.createElement('option');
                    option.textContent = phone;
                    phoneSelect.appendChild(option);
                });
            } catch (error) {
                alert('Error fetching foundry details.');
            }
        }
    }
});
