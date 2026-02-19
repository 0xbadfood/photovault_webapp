let users = [];
let userToDelete = null;

async function fetchUsers() {
    try {
        const response = await fetch('/api/admin/users');
        if (!response.ok) {
            if (response.status === 401 || response.status === 403) {
                window.location.href = '/'; // Redirect if not unauthorized
                return;
            }
            throw new Error('Failed to fetch users');
        }
        const data = await response.json();
        users = data.users;
        renderUsers();
    } catch (err) {
        console.error(err);
        alert('Error loading users: ' + err.message);
    }
}

function renderUsers() {
    const tbody = document.querySelector('#usersTable tbody');
    tbody.innerHTML = '';

    users.forEach(user => {
        const tr = document.createElement('tr');

        const outputStatus = user.status === 'active'
            ? '<span style="color: #4cd964">Active</span>'
            : '<span style="color: #ff3b30">Revoked</span>';

        const isAdmin = user.is_admin ? '<span style="color: #ffd60a">Admin</span>' : 'User';

        tr.innerHTML = `
            <td>${user.id}</td>
            <td>${user.email}</td>
            <td>${outputStatus}</td>
            <td>${isAdmin}</td>
            <td>
                ${!user.is_admin ? `
                    <button class="btn btn-warning" onclick="toggleStatus('${user.email}', '${user.status}')">
                        ${user.status === 'active' ? 'Revoke' : 'Activate'}
                    </button>
                    <button class="btn btn-danger" onclick="showDeleteModal('${user.email}')">Delete</button>
                ` : '<span style="color: #888">No actions</span>'}
            </td>
        `;
        tbody.appendChild(tr);
    });
}

async function addUser() {
    const email = document.getElementById('newEmail').value;
    const password = document.getElementById('newPassword').value;
    const confirmPassword = document.getElementById('confirmNewPassword').value;

    if (!email || !password || !confirmPassword) {
        alert('Please fill in all fields');
        return;
    }

    if (password !== confirmPassword) {
        alert('Passwords do not match');
        return;
    }

    try {
        const response = await fetch('/api/admin/users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password })
        });

        if (!response.ok) {
            const data = await response.json();
            throw new Error(data.error || 'Failed to create user');
        }

        closeModal('addUserModal');
        document.getElementById('newEmail').value = '';
        document.getElementById('newPassword').value = '';
        document.getElementById('confirmNewPassword').value = '';
        fetchUsers();
        alert('User created. They will be required to change their password on first login.');

    } catch (err) {
        alert(err.message);
    }
}

async function toggleStatus(email, currentStatus) {
    const newStatus = currentStatus === 'active' ? 'revoked' : 'active';
    if (!confirm(`Are you sure you want to change status to ${newStatus}?`)) return;

    try {
        const response = await fetch('/api/admin/users/status', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ userid: email, status: newStatus })
        });

        if (!response.ok) throw new Error('Failed to update status');
        fetchUsers();
    } catch (err) {
        alert(err.message);
    }
}

function showDeleteModal(email) {
    userToDelete = email;
    document.getElementById('deleteUserEmail').textContent = email;
    document.getElementById('destroyDataCheck').checked = false;
    document.getElementById('deleteUserModal').style.display = 'flex';
}

async function confirmDeleteUser() {
    if (!userToDelete) return;

    const destroyData = document.getElementById('destroyDataCheck').checked;

    try {
        const response = await fetch('/api/admin/users/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ userid: userToDelete, destroy_data: destroyData })
        });

        if (!response.ok) {
            const data = await response.json();
            throw new Error(data.error || 'Failed to delete user');
        }

        closeModal('deleteUserModal');
        fetchUsers();
    } catch (err) {
        alert(err.message);
    }
}

function showAddUserModal() {
    document.getElementById('addUserModal').style.display = 'flex';
}

function closeModal(modalId) {
    document.getElementById(modalId).style.display = 'none';
}

// Close modals on outside click
window.onclick = function (event) {
    if (event.target.classList.contains('modal')) {
        event.target.style.display = "none";
    }
}

// Init
fetchUsers();

async function logout() {
    try {
        await fetch('/api/logout', { method: 'POST' });
        window.location.href = '/';
    } catch (e) {
        console.error(e);
        alert('Logout failed');
    }
}
