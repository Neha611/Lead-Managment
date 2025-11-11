frappe.provide('email_validation_widget');

email_validation_widget = {
	categoryChart: null,
	timelineChart: null,
	currentDateRange: 24,

	init: function() {
		const me = this;

		// Date range selector change handler
		$('#validation-date-range').on('change', function() {
			me.currentDateRange = $(this).val();
			me.loadDashboard();
		});

		// View all audits button
		$('#view-all-audits').on('click', function() {
			frappe.set_route('List', 'Email Validation Audit');
		});

		// Initial load
		me.loadDashboard();

		// Auto-refresh every 5 minutes
		setInterval(function() {
			me.loadDashboard();
		}, 5 * 60 * 1000);
	},

	loadDashboard: function() {
		const me = this;

		// Show loading state
		me.showLoading();

		// Fetch metrics
		frappe.call({
			method: 'crm_override.crm_override.validation_dashboard.get_validation_metrics',
			args: {
				date_range: me.currentDateRange
			},
			callback: function(r) {
				if (r.message) {
					me.renderDashboard(r.message);
				}
			}
		});

		// Fetch top senders
		frappe.call({
			method: 'crm_override.crm_override.validation_dashboard.get_top_senders',
			args: {
				date_range: me.currentDateRange,
				limit: 10
			},
			callback: function(r) {
				if (r.message) {
					me.renderTopSenders(r.message);
				}
			}
		});
	},

	showLoading: function() {
		$('#total-validations').text('...');
		$('#lead-count').text('...');
		$('#non-lead-count').text('...');
		$('#query-count').text('...');
	},

	renderDashboard: function(data) {
		const me = this;

		// Update summary cards
		$('#total-validations').text(data.total_validations || 0);
		$('#lead-count').text(data.categories.Lead.count || 0);
		$('#lead-percentage').text(`${data.categories.Lead.percentage}%`);
		$('#non-lead-count').text(data.categories['Non Lead'].count || 0);
		$('#non-lead-percentage').text(`${data.categories['Non Lead'].percentage}%`);
		$('#query-count').text(data.categories.Query.count || 0);
		$('#query-percentage').text(`${data.categories.Query.percentage}%`);

		// Update conversion rates
		$('#lead-to-opportunity').text(data.conversion_rates.lead_to_opportunity);
		$('#query-to-lead').text(data.conversion_rates.query_to_lead);
		$('#non-lead-to-lead').text(data.conversion_rates.non_lead_to_lead);

		// Render category distribution chart
		me.renderCategoryChart(data.categories);

		// Render timeline chart
		me.renderTimelineChart(data.validation_timeline);
	},

	renderCategoryChart: function(categories) {
		const me = this;

		// Destroy existing chart
		if (me.categoryChart) {
			me.categoryChart = null;
		}

		// Prepare data
		const chartData = {
			labels: ['Lead', 'Non Lead', 'Query'],
			datasets: [{
				values: [
					categories.Lead.count,
					categories['Non Lead'].count,
					categories.Query.count
				]
			}]
		};

		// Create chart
		$('#category-chart').empty();
		me.categoryChart = new frappe.Chart('#category-chart', {
			data: chartData,
			type: 'pie',
			height: 250,
			colors: ['#28a745', '#dc3545', '#ffc107']
		});
	},

	renderTimelineChart: function(timeline) {
		const me = this;

		// Destroy existing chart
		if (me.timelineChart) {
			me.timelineChart = null;
		}

		if (!timeline || timeline.length === 0) {
			$('#timeline-chart').html('<p class="text-muted text-center">No data available</p>');
			return;
		}

		// Prepare data
		const labels = timeline.map(t => {
			// Format: "2025-01-10 10:00:00" -> "Jan 10, 10:00"
			const d = new Date(t.hour);
			return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) +
			       ', ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
		});

		const chartData = {
			labels: labels,
			datasets: [
				{
					name: 'Lead',
					values: timeline.map(t => t.Lead || 0)
				},
				{
					name: 'Non Lead',
					values: timeline.map(t => t['Non Lead'] || 0)
				},
				{
					name: 'Query',
					values: timeline.map(t => t.Query || 0)
				}
			]
		};

		// Create chart
		$('#timeline-chart').empty();
		me.timelineChart = new frappe.Chart('#timeline-chart', {
			data: chartData,
			type: 'bar',
			height: 300,
			colors: ['#28a745', '#dc3545', '#ffc107'],
			axisOptions: {
				xAxisMode: 'tick',
				xIsSeries: true
			},
			barOptions: {
				stacked: 1
			}
		});
	},

	renderTopSenders: function(senders) {
		const tbody = $('#top-senders-table tbody');
		tbody.empty();

		if (!senders || senders.length === 0) {
			tbody.append(`
				<tr>
					<td colspan="3" class="text-center text-muted">No data available</td>
				</tr>
			`);
			return;
		}

		senders.forEach(function(sender) {
			// Category badge color
			let badgeClass = 'badge-success';
			if (sender.category === 'Non Lead') badgeClass = 'badge-danger';
			if (sender.category === 'Query') badgeClass = 'badge-warning';

			tbody.append(`
				<tr>
					<td>${sender.sender_email}</td>
					<td><span class="badge ${badgeClass}">${sender.category}</span></td>
					<td>${sender.count}</td>
				</tr>
			`);
		});
	}
};

// Initialize when DOM is ready
$(document).ready(function() {
	// Check if we're on the right page
	if ($('.email-validation-dashboard').length > 0) {
		email_validation_widget.init();
	}
});
