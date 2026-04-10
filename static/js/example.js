document.addEventListener('DOMContentLoaded', function() {
    const endDate = new Date();
    const startDate = new Date();
    // startDate.setMonth(startDate.getMonth() - 1);
    startDate.setDate(startDate.getDate() - 30);

    // Initialize the start datetime picker
    flatpickr(document.getElementById('start'), {
        enableTime: true,
        dateFormat: "Y-m-d\\TH:i:S",
        defaultHour: 0,
        altFormat: "Y-m-d\\TH:i:S",
        allowInput: true,
        time_24hr: true,
        defaultDate: startDate, // Set the default start date
    });

    // Initialize the end datetime picker
    flatpickr(document.getElementById('end'), {
        enableTime: true,
        dateFormat: "Y-m-d\\TH:i:S",
        defaultHour: 0,
        altFormat: "Y-m-d\\TH:i:S",
        allowInput: true,
        time_24hr: true,
        defaultDate: endDate, // Set the default end date
    });

    var baseUrl = isOvsa ? '/flarelist' : '';

    // Function to render table with data
    function renderTable(data) {
        let tableBody = '';
        data.forEach((item) => {
            let row = '<tr>';
            ['_id'].forEach((key) => {
                row += '<td>' + (item[key] || '') + '</td>';
            }); // Handle null values
            // Add a cell for the flare_id with an onclick event
            let flareStyle = (item['Fpk_over_10sfu'] === 'orange') ? ' style="color:orange;"' : '';
            row += '<td><a href="#" class="flare-id-link" data-flare-id="' + item['flare_id'] + '"' + flareStyle + '>' + item['flare_id'] + '</a></td>';

            ['start', 'peak', 'end', 'GOES_class', 'Fpk_XP_3GHz', 'Fpk_XP_11GHz', 
                'link_dspec_TP', 'link_dspec_data_TP', 'link_dspec_XP', 'link_dspec_data_XP', 'link_movie', 'link_fits'].forEach((key) => {
                row += '<td>' + (item[key] || '') + '</td>'; // Handle null values
            });

            row += '</tr>';
            tableBody += row; // Append the row to the tableBody
        });
        // Show the table and update its content
        $('#flare-list').show();
        $('#flare-list > tbody').html(tableBody);
        attachFlareIdClickEvent()
    }

    // Function to attach click events to flare_id links
    function attachFlareIdClickEvent() {
        $('.flare-id-link').on('click', function(e) {
            e.preventDefault(); // Prevent the default anchor action
            var flareId = $(this).data('flare-id');
            fetchAndDisplayFlareData(flareId);
        });
    }

    function fetchAndDisplayFlareData(flareId) {
        $.ajax({
            url: baseUrl + `/fetch-spectral-data-xp/${flareId}`,
            method: 'GET',
            success: function(response) {
                var plotData = JSON.parse(response.plot_data_ID);
                var isSpecTP = false;

                var config = {
                    modeBarButtonsToAdd: [
                        {
                            name: 'Toggle Log-Y Scale',
                            icon: Plotly.Icons.pencil,
                            click: function (gd) {
                                var currentType = gd.layout.yaxis.type;
                                var newType = currentType === 'log' ? 'linear' : 'log';
                                Plotly.relayout(gd, 'yaxis.type', newType);
                            }
                        },
                        {
                            name: 'Toggle spec_XP/TP',
                            icon: Plotly.Icons.pencil, // Placeholder icon initially
                            click: function () {
                                isSpecTP = !isSpecTP;
                                if (isSpecTP) {
                                    console.log("Fetching spec_TP data...");
                                    fetchAndDisplaySpecTP(flareId);
                                } else {
                                    console.log("Returning to original plot data...");
                                    Plotly.newPlot('plot-container', plotData.data, plotData.layout, config).then(function () {
                                        replaceIconWithSVG(); // Replace the placeholder icon after rendering
                                    });
                                }
                            }
                        }
                    ],
                    displaylogo: false,
                    responsive: true
                };
                // Use the config object here to include the custom button
                Plotly.newPlot('plot-container', plotData.data, plotData.layout, config).then(function () {
                    replaceIconWithSVG(); // Replace the placeholder icon with your custom SVG after rendering
                });
                // Scroll to the plot container after the plot has been created
                document.getElementById('plot-container').scrollIntoView({
                    behavior: 'smooth',
                    block: 'start'
                });
            },
            error: function(xhr, status, error) {
                console.error("Failed to fetch data for Flare ID:", flareId, status, error);
            }
        });
    }

    // Function to replace the placeholder icon with your custom SVG after rendering 
    function replaceIconWithSVG() {
        const modebar = document.querySelector('.modebar-container');
        const toggleButton = modebar ? modebar.querySelector('[data-title="Toggle spec_XP/TP"]') : null;
        if (toggleButton) {
            // Create an <img> element for the custom icon
            const img = document.createElement('img');
            img.src = baseUrl + '/static/images/x.svg'; // Path to your SVG icon file
            img.style.width = '20px';
            img.style.height = '20px';
            img.alt = 'Toggle spec_XP';
            // Clear the existing content and add the new custom SVG icon
            toggleButton.innerHTML = ''; // Remove any existing content (e.g., the old icon)
            toggleButton.appendChild(img); // Add the custom SVG image as the new content
            console.log("Icon replaced with custom SVG.");
        } else {
            console.error("Toggle button not found in the modebar.");
        }
    }

    function fetchAndDisplaySpecTP(flareId) {
        $.ajax({
            url: baseUrl + `/fetch-spectral-data-tp/${flareId}`,
            method: 'GET',
            success: function(response) {
                var plotData = JSON.parse(response.plot_data_ID);
                Plotly.react('plot-container', plotData.data, plotData.layout);
            },
            error: function(xhr, status, error) {
                console.error("Failed to fetch spec_TP data for Flare ID:", flareId, status, error);
            }
        });
    }

    // Function to fetch data based on given start and end dates
    function fetchData(start, end) {
        $.ajax({
            url: baseUrl + '/api/flare/query',
            type: "POST",
            data: { 'start': start, 'end': end },
            dataType: "json",
            success: function(response) {
                if (response.error) {
                    console.error(response.error);
                } else {
                    renderTable(response.result);
                    attachFlareIdClickEvent()
                }
            },
            error: function(xhr, status, error) {
                console.error("Error occurred: " + error);
                showError("An error occurred while processing your request.");
            }
        });
    }

    // On query button click, fetch data for the given date range
    $('#query-btn').on('click', function(e) {
        e.preventDefault();
        let start = $('#start').val();
        let end = $('#end').val();
        fetchData(start, end);
    });

    // Automatically fetch data based on the selected dates in the datetime pickers on page load
    (function autoFetchData() {
        // Retrieve the flatpickr instances
        const startPicker = document.querySelector("#start")._flatpickr;
        const endPicker = document.querySelector("#end")._flatpickr;
        // Get the selected dates from the datetime pickers
        // Ensure to check if the picker has selected dates to avoid errors
        const start = startPicker.selectedDates[0] ? startPicker.selectedDates[0].toISOString().split('T')[0] : null;
        const end = endPicker.selectedDates[0] ? endPicker.selectedDates[0].toISOString().split('T')[0] : null;
        // Check if both start and end dates are selected
        if (start && end) {
            // Fetch data using the start and end dates
            fetchData(start, end);
        } else {
            console.error("Start or end date is missing.");
        }
    })();

});
