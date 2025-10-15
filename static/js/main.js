// Main JavaScript for Parking System

document.addEventListener('DOMContentLoaded', function() {
    // Initialize tooltips
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    var tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });

    // Auto-dismiss alerts after 5 seconds
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(alert => {
        setTimeout(() => {
            const bsAlert = new bootstrap.Alert(alert);
            bsAlert.close();
        }, 5000);
    });

    // Form validation enhancement
    const forms = document.querySelectorAll('form');
    forms.forEach(form => {
        form.addEventListener('submit', function(e) {
            const requiredFields = form.querySelectorAll('[required]');
            let isValid = true;

            requiredFields.forEach(field => {
                if (!field.value.trim()) {
                    isValid = false;
                    field.classList.add('is-invalid');
                } else {
                    field.classList.remove('is-invalid');
                }
            });

            if (!isValid) {
                e.preventDefault();
                showToast('Vui lòng điền đầy đủ thông tin bắt buộc', 'warning');
            }
        });
    });

    // License plate format validation
    const licensePlateInputs = document.querySelectorAll('input[pattern="\\d{2}[A-Z]-\\d{3}\\.\\d{2}"]');
    licensePlateInputs.forEach(input => {
        input.addEventListener('blur', function() {
            const pattern = /^\d{2}[A-Z]-\d{3}\.\d{2}$/;
            if (this.value && !pattern.test(this.value)) {
                this.classList.add('is-invalid');
                showToast('Biển số xe không đúng định dạng (VD: 51A-123.45)', 'warning');
            } else {
                this.classList.remove('is-invalid');
            }
        });
    });

    // Image preview and ANPR functionality
    const imageInputs = document.querySelectorAll('input[type="file"][accept="image/*"]');
    imageInputs.forEach(input => {
        input.addEventListener('change', function(e) {
            const file = e.target.files[0];
            if (file) {
                const previewId = this.id + 'Preview';
                const preview = document.getElementById(previewId) || document.getElementById('preview');
                const reader = new FileReader();

                reader.onload = function(e) {
                    if (preview) {
                        preview.src = e.target.result;
                        preview.style.display = 'block';
                    }

                    // Auto-submit for ANPR recognition
                    const formData = new FormData();
                    formData.append('image', file);

                    fetch('/recognize_license_plate', {
                        method: 'POST',
                        body: formData
                    })
                    .then(response => response.json())
                    .then(data => {
                        if (data.license_plate && data.license_plate !== "Không nhận diện được") {
                            const licensePlateField = document.getElementById('license_plate');
                            if (licensePlateField) {
                                licensePlateField.value = data.license_plate;
                                showToast(`Đã nhận diện biển số: ${data.license_plate}`, 'success');
                            }
                        } else if (data.error) {
                            showToast('Lỗi nhận diện biển số: ' + data.error, 'danger');
                        }
                    })
                    .catch(error => {
                        console.error('Error:', error);
                        showToast('Lỗi kết nối khi nhận diện biển số', 'danger');
                    });
                };

                reader.readAsDataURL(file);
            }
        });
    });

    // Balance format helper
    const balanceInputs = document.querySelectorAll('input[type="number"][min="10000"]');
    balanceInputs.forEach(input => {
        input.addEventListener('input', function() {
            const value = parseFloat(this.value);
            if (value < 10000) {
                this.classList.add('is-invalid');
            } else {
                this.classList.remove('is-invalid');
            }
        });
    });

    // Tab persistence
    const tabPanes = document.querySelectorAll('.tab-pane');
    if (tabPanes.length > 0) {
        const activeTab = localStorage.getItem('activeTab');
        if (activeTab) {
            const tab = document.querySelector(`[data-bs-target="${activeTab}"]`);
            if (tab) {
                new bootstrap.Tab(tab).show();
            }
        }

        // Save active tab
        const tabTriggers = document.querySelectorAll('[data-bs-toggle="tab"]');
        tabTriggers.forEach(trigger => {
            trigger.addEventListener('click', function() {
                localStorage.setItem('activeTab', this.getAttribute('data-bs-target'));
            });
        });
    }
});

// Toast notification function
function showToast(message, type = 'info') {
    // Remove existing toasts
    const existingToasts = document.querySelectorAll('.toast-container');
    existingToasts.forEach(toast => toast.remove());

    const toastContainer = document.createElement('div');
    toastContainer.className = 'toast-container position-fixed top-0 end-0 p-3';
    toastContainer.style.zIndex = '9999';

    const toastHtml = `
        <div class="toast align-items-center text-white bg-${type === 'success' ? 'success' : type === 'warning' ? 'warning' : type === 'danger' ? 'danger' : 'info'} border-0 show" role="alert">
            <div class="d-flex">
                <div class="toast-body">
                    ${message}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        </div>
    `;

    toastContainer.innerHTML = toastHtml;
    document.body.appendChild(toastContainer);

    // Auto remove after 5 seconds
    setTimeout(() => {
        toastContainer.remove();
    }, 5000);
}

// Utility function for number formatting
function formatNumber(num) {
    return new Intl.NumberFormat('vi-VN').format(num);
}

// Utility function for date formatting
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('vi-VN') + ' ' + date.toLocaleTimeString('vi-VN');
}

// Export functions for global use
window.ParkingSystem = {
    showToast,
    formatNumber,
    formatDate
};