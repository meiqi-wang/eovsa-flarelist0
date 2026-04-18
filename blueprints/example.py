import numpy as np
import pandas as pd
from flask import Flask, Blueprint, render_template, request, jsonify, url_for
import plotly.express as px
import plotly.graph_objects as go
from bs4 import BeautifulSoup
import socket
import json
import plotly
import os
import re
import mysql.connector
from astropy.time import Time
import requests


def check_url_exists(url):
    try:
        response = requests.head(url, allow_redirects=True, timeout=5)
        return response.status_code == 200
    except requests.RequestException:
        return False


example = Blueprint('example', __name__, template_folder='templates')
VALIDATE_QUERY_LINKS = os.getenv('FLARE_VALIDATE_QUERY_LINKS', '').strip().lower() in ('1', 'true', 'yes', 'on')


def _is_nonempty(value):
    return value not in (None, '', 'None', 'nan')


def _build_icon_link(url, icon_url, alt):
    return f'<div style="text-align: center;"><a href="{url}"><img src="{icon_url}" alt="{alt}" style="width:20px;height:20px;"></a></div>'


def _jd_to_isot_seconds(jd_value):
    return Time(float(jd_value), format='jd').isot.split('.')[0]


_WIKI_BASE = "https://ovsa.njit.edu/wiki/index.php"
_wiki_year_cache = {}
_url_exists_cache = {}
_URL_PATTERN = re.compile(r'(https?://[^\s<>"\']+)')


def _build_wiki_year_url(year):
    return f"{_WIKI_BASE}/{year}"


def _check_url_exists_cached(url):
    if not url:
        return False
    if url in _url_exists_cache:
        return _url_exists_cache[url]
    ok = check_url_exists(url)
    _url_exists_cache[url] = ok
    return ok


def _normalize_wiki_href(href):
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("//"):
        return f"https:{href}"
    if href.startswith("/"):
        return f"https://ovsa.njit.edu{href}"
    if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', href):
        return href
    return f"{_WIKI_BASE}/{href.lstrip('/')}"


def _format_comment_html(comment_cell):
    # Preserve rendered wiki links in comment cells and make plain URLs clickable.
    inner_html = ''.join(str(node) for node in comment_cell.contents).strip()
    if not inner_html:
        return ""

    soup = BeautifulSoup(inner_html, "html.parser")

    for anchor in soup.find_all("a"):
        href = _normalize_wiki_href(anchor.get("href"))
        if not href:
            continue
        anchor["href"] = href
        anchor["target"] = "_blank"
        anchor["rel"] = "noopener noreferrer"

    for text_node in list(soup.find_all(string=True)):
        if text_node.parent and text_node.parent.name == "a":
            continue
        text_val = str(text_node)
        if not _URL_PATTERN.search(text_val):
            continue

        replaced_html = _URL_PATTERN.sub(
            r'<a href="\1" target="_blank" rel="noopener noreferrer">\1</a>',
            text_val
        )
        fragment = BeautifulSoup(replaced_html, "html.parser")
        for new_node in reversed(fragment.contents):
            text_node.insert_after(new_node)
        text_node.extract()

    return str(soup).strip()


def _extract_flare_location_url(row, flare_id_key):
    flare_id_key = (flare_id_key or "").strip()
    flare_id_key_lower = flare_id_key.lower()

    # Match only wiki file links containing both flare id and "FL" token.
    for anchor in row.find_all("a"):
        href = _normalize_wiki_href(anchor.get("href"))
        if not href:
            continue
        href_lower = href.lower()
        if (
            "index.php/file:" in href_lower
            and flare_id_key_lower
            and flare_id_key_lower in href_lower
            and "fl" in href_lower
        ):
            return href

    # No valid flare-location link found on this row.
    return ""


def _load_wiki_year_cache(year):
    if year in _wiki_year_cache:
        return

    page_url = _build_wiki_year_url(year)
    cache_item = {
        "comments": {},
        "flare_locations": {},
    }

    try:
        response = requests.get(page_url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        for table in soup.find_all("table", {"class": "wikitable"}):
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue

                date_text = cells[0].get_text(" ", strip=True).replace("/", "-")
                time_text = cells[1].get_text(" ", strip=True)
                time_text = time_text.replace("UTC", "").replace("UT", "").strip()

                date_match = re.search(r'(\d{4}-\d{2}-\d{2})', date_text)
                time_match = re.search(r'(\d{2}:\d{2})(?::(\d{2}))?', time_text)
                if not date_match or not time_match:
                    continue

                hhmmss = f"{time_match.group(1)}:{time_match.group(2) or '00'}"
                flare_key = (
                    date_match.group(1).replace("-", "")
                    + hhmmss.replace(":", "")
                )

                cache_item["comments"][flare_key] = _format_comment_html(cells[-1]) if len(cells) > 3 else ""
                cache_item["flare_locations"][flare_key] = _extract_flare_location_url(row, flare_key)
    except requests.RequestException:
        pass

    _wiki_year_cache[year] = cache_item


def _fetch_wiki_comment(flare_id_str):
    year = flare_id_str[:4]
    _load_wiki_year_cache(year)

    flare_key_exact = flare_id_str[:14]
    flare_key_minute = flare_id_str[:12] + "00"
    comment_map = _wiki_year_cache.get(year, {}).get("comments", {})

    return (
        comment_map.get(flare_key_exact)
        or comment_map.get(flare_key_minute)
        or ""
    )


def _fetch_wiki_flare_location(flare_id_str):
    year = flare_id_str[:4]
    _load_wiki_year_cache(year)

    flare_key_exact = flare_id_str[:14]
    flare_key_minute = flare_id_str[:12] + "00"
    flare_location_map = _wiki_year_cache.get(year, {}).get("flare_locations", {})

    return (
        flare_location_map.get(flare_key_exact)
        or flare_location_map.get(flare_key_minute)
        or ""
    )


def get_eo_flare_list_MySQL(start_utc, end_utc):
    """
    info from MySQL
    """
    connection = mysql.connector.connect(
        host=os.getenv('FLARE_DB_HOST'),
        database=os.getenv('FLARE_DB_DATABASE'),
        user=os.getenv('FLARE_DB_USER'),
        password=os.getenv('FLARE_DB_PASSWORD')
    )

    t_st = Time(start_utc).jd
    t_ed = Time(end_utc).jd
    cursor = connection.cursor()
    query_with_flags = """
        SELECT Flare_ID, Flare_class, EO_tstart, EO_tpeak, EO_tend,
               depec_imgfile_TP, depec_datafile_TP, depec_imgfile_XP, depec_datafile_XP,
               Fpk_XP, freq_at_Fpk_XP, Fpk_over_10sfu, has_ql_movie, has_fits
        FROM EOVSA_flare_list_wiki_tb
        WHERE EO_tstart <= %s AND %s <= EO_tend
        ORDER BY EO_tstart
    """
    query_without_flags = """
        SELECT Flare_ID, Flare_class, EO_tstart, EO_tpeak, EO_tend,
               depec_imgfile_TP, depec_datafile_TP, depec_imgfile_XP, depec_datafile_XP,
               Fpk_XP, freq_at_Fpk_XP
        FROM EOVSA_flare_list_wiki_tb
        WHERE EO_tstart <= %s AND %s <= EO_tend
        ORDER BY EO_tstart
    """
    has_link_flags = True
    try:
        cursor.execute(query_with_flags, (t_ed, t_st))
        rows = cursor.fetchall()
    except mysql.connector.Error:
        has_link_flags = False
        cursor.execute(query_without_flags, (t_ed, t_st))
        rows = cursor.fetchall()
    cursor.close()
    connection.close()

    result = []
    keys = ['_id', 'start', 'end', 'link']
    keys = ['_id', 'flare_id', 'start', 'peak', 'end', 'GOES_class', 'link_dspec_data', 'link_movie', 'link_fits']
    keys = ['_id', 'flare_id', 'start', 'peak', 'end', 'GOES_class', 'link_dspec_TP', 'link_dspec_data_TP', 'link_dspec_XP', 'link_dspec_data_XP', 'link_movie', 'link_fits']
    ql_symbol_url = url_for('static', filename='images/ql.svg')
    dl_symbol_url = url_for('static', filename='images/dl.svg')

    if rows:
        for i, row in enumerate(rows):
            (
                flare_id_val, goes_class, eo_tstart, eo_tpeak, eo_tend,
                img_tp, data_tp, img_xp, data_xp, fpk_tot, pk_freq, fpk_over_10sfu, *link_flags
            ) = row

            flare_id_str = str(flare_id_val)
            has_ql_movie = int(link_flags[0]) if has_link_flags and link_flags else 0
            has_fits = int(link_flags[1]) if has_link_flags and len(link_flags) > 1 else 0

            link_dspec_str_TP = f'https://www.ovsa.njit.edu/wiki/index.php/File:{img_tp}' if _is_nonempty(img_tp) else None
            link_dspec_data_str_TP = f'https://ovsa.njit.edu/events/{flare_id_str[0:4]}/{data_tp}' if _is_nonempty(data_tp) else None
            link_dspec_str_XP = f'https://www.ovsa.njit.edu/wiki/index.php/File:{img_xp}' if _is_nonempty(img_xp) else None
            link_dspec_data_str_XP = f'https://ovsa.njit.edu/events/{flare_id_str[0:4]}/{data_xp}' if _is_nonempty(data_xp) else None

            link_movie_str = f'https://www.ovsa.njit.edu/SynopticImg/eovsamedia/eovsa-browser/{flare_id_str[0:4]}/{flare_id_str[4:6]}/{flare_id_str[6:8]}/eovsa.lev1_mbd_12s.flare_id_{flare_id_str}.mp4'
            link_fits_str = f'https://www.ovsa.njit.edu/fits/flares/{flare_id_str[0:4]}/{flare_id_str[4:6]}/{flare_id_str[6:8]}/{flare_id_str}/'

            link_dspec_TP = None
            link_dspec_data_TP = None
            if link_dspec_str_TP and link_dspec_data_str_TP:
                if (not VALIDATE_QUERY_LINKS) or check_url_exists(link_dspec_str_TP):
                    link_dspec_TP = _build_icon_link(link_dspec_str_TP, ql_symbol_url, "DSpec")
                    link_dspec_data_TP = _build_icon_link(link_dspec_data_str_TP, dl_symbol_url, "DSpec_Data")

            link_dspec_XP = None
            link_dspec_data_XP = None
            if link_dspec_str_XP and link_dspec_data_str_XP:
                if (not VALIDATE_QUERY_LINKS) or check_url_exists(link_dspec_str_XP):
                    link_dspec_XP = _build_icon_link(link_dspec_str_XP, ql_symbol_url, "DSpec")
                    link_dspec_data_XP = _build_icon_link(link_dspec_data_str_XP, dl_symbol_url, "DSpec_Data")

            link_movie = None
            link_fits = None
            flare_location_url = _fetch_wiki_flare_location(flare_id_str)
            link_flare_location = (
                _build_icon_link(flare_location_url, ql_symbol_url, "Flare_Location")
                if _check_url_exists_cached(flare_location_url)
                else None
            )
            if has_link_flags:
                if has_ql_movie:
                    link_movie = _build_icon_link(link_movie_str, ql_symbol_url, "QL_Movie")
                if has_fits:
                    link_fits = _build_icon_link(link_fits_str, dl_symbol_url, "FITS")
            elif (not VALIDATE_QUERY_LINKS) or check_url_exists(link_movie_str):
                link_movie = _build_icon_link(link_movie_str, ql_symbol_url, "QL_Movie")
                link_fits = _build_icon_link(link_fits_str, dl_symbol_url, "FITS")

            raw_fpk_color = str(fpk_over_10sfu).strip().lower() if fpk_over_10sfu is not None else ''
            if raw_fpk_color in ('blue', 'yellow'):
                normalized_fpk_color = raw_fpk_color
            elif raw_fpk_color in ('orange', 'true', '1'):
                normalized_fpk_color = 'blue'
            else:
                try:
                    normalized_fpk_color = 'blue' if float(fpk_tot) > 10.0 else 'yellow'
                except (TypeError, ValueError):
                    normalized_fpk_color = 'yellow'

            result.append({
                '_id': i + 1,
                'flare_id': int(flare_id_val),
                'start': _jd_to_isot_seconds(eo_tstart),
                'peak': _jd_to_isot_seconds(eo_tpeak),
                'end': _jd_to_isot_seconds(eo_tend),
                'GOES_class': goes_class,
                'Fpk_XP': f'<div style="text-align: center;">{fpk_tot}</div>',
                'Pk_freq_GHz': f'<div style="text-align: center;">{pk_freq}</div>',
                'comments': f'<div style="color: black;">{_fetch_wiki_comment(flare_id_str)}</div>',
                'link_dspec_TP': link_dspec_TP,
                'link_dspec_data_TP': link_dspec_data_TP,
                'link_dspec_XP': link_dspec_XP,
                'link_dspec_data_XP': link_dspec_data_XP,
                'link_flare_location': link_flare_location,
                'link_movie': link_movie,
                'link_fits': link_fits,
                'Fpk_over_10sfu': normalized_fpk_color
            })
    return result


@example.route("/api/flare/query", methods=['POST'])
def get_flare_list_from_database():
    try:
        start = request.form['start']
        end = request.form['end']
        if not start or not end:
            raise ValueError("Start and end times are required.")

        result = get_eo_flare_list_MySQL(start, end)
        return jsonify({"result": result})

    except Exception as e:
        # Log the exception for debugging
        print(f"Error: {e}")
        # Return a JSON response with the error message
        return jsonify({"error": str(e)}), 500


@example.route('/fetch-spectral-data-tp/<flare_id>', methods=['GET'])
# #####=========================click on flare ID and show its corresponding flux curves
def fetch_spectral_data_tp(flare_id):
    #####=========================
    ##Connect to the MySQL database

    connection = mysql.connector.connect(
        host=os.getenv('FLARE_DB_HOST'),
        database=os.getenv('FLARE_LC_DB_DATABASE'),
        user=os.getenv('FLARE_DB_USER'),
        password=os.getenv('FLARE_DB_PASSWORD')
    )

    given_flare_id = int(flare_id)

    cursor = connection.cursor()
    #####=========================
    cursor.execute("SELECT * FROM time_QL_TP WHERE Flare_ID = %s", (given_flare_id,))
    records = cursor.fetchall()
    # jd_times = []  # List to store jd_time values
    # for record in records:
    #     # Assuming jd_time is the third column (index 2) in the table
    #     jd_time = record[2]
    #     jd_times.append(jd_time)
    # time1 = jd_times
    time1 = [record[2] for record in records]

    #####=========================
    cursor.execute("SELECT * FROM freq_QL_TP WHERE Flare_ID = %s", (given_flare_id,))
    records = cursor.fetchall()
    # Extract the values from the fetched records
    fghz = [record[2] for record in records]

    #####=========================
    cursor.execute("SELECT * FROM flux_QL_TP WHERE Flare_ID = %s", (given_flare_id,))
    records = cursor.fetchall()

    spec_QL = []
    # Iterate over the retrieved records and reconstruct the array
    for record in records:
        Flare_ID, Index_f, Index_t, flux = record
        while len(spec_QL) <= Index_f:
            spec_QL.append([])
        while len(spec_QL[Index_f]) <= Index_t:
            spec_QL[Index_f].append(None)
        spec_QL[Index_f][Index_t] = flux

    spec = np.array(spec_QL)

    cursor.close()
    connection.close()

    #####=========================
    from astropy.time import Time
    tim_plt_datetime = pd.to_datetime(Time(time1, format='jd').isot)
    # tim_plt_datetime = ["2021-01-01T00:00:00", "2021-01-01T00:01:00", "2021-01-01T00:02:00"]

    spec_plt_log = spec
    freq_plt = fghz

    # Create the Plotly figure
    fig = go.Figure()

    # Plot the spectral data
    # fig.add_trace(go.Scatter(x=tim_plt_datetime, y=spec_plt_log, mode='lines', name='Spectral Data'))
    fig.add_trace(go.Scatter(x=tim_plt_datetime, y=spec_plt_log[0, :], mode='lines', name=f"{freq_plt[0]:.1f} GHz"))
    fig.add_trace(go.Scatter(x=tim_plt_datetime, y=spec_plt_log[1, :], mode='lines', name=f"{freq_plt[1]:.1f} GHz"))
    fig.add_trace(go.Scatter(x=tim_plt_datetime, y=spec_plt_log[2, :], mode='lines', name=f"{freq_plt[2]:.1f} GHz"))
    fig.add_trace(go.Scatter(x=tim_plt_datetime, y=spec_plt_log[3, :], mode='lines', name=f"{freq_plt[3]:.1f} GHz"))

    # Update layout
    fig.update_layout(
        title=f'Flux_TP Data for Flare ID: {flare_id}',
        xaxis_title="Time [UT]",
        yaxis_title="Flux_TP [sfu]",
        xaxis_tickformat='%H:%M:%S',
        template="plotly"  # or choose another template that fits your web design
    )

    # Convert Plotly figure to HTML
    plot_html_ID = fig.to_html(full_html=False)  # , include_plotlyjs=False
    print(f"Flare ID {flare_id}: fetch-spectral-data-tp success")

    # # Return the plot's HTML for dynamic insertion into the webpage
    plot_data_ID = json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)
    return jsonify({"plot_data_ID": plot_data_ID})




@example.route('/fetch-spectral-data-xp/<flare_id>', methods=['GET'])
# #####=========================click on flare ID and show its corresponding flux curves
def fetch_spectral_data_xp(flare_id):
    #####=========================
    ##Connect to the MySQL database

    connection = mysql.connector.connect(
        host=os.getenv('FLARE_DB_HOST'),
        database=os.getenv('FLARE_LC_DB_DATABASE'),
        user=os.getenv('FLARE_DB_USER'),
        password=os.getenv('FLARE_DB_PASSWORD')
    )

    given_flare_id = int(flare_id)

    cursor = connection.cursor()
    #####=========================
    cursor.execute("SELECT * FROM time_QL_XP WHERE Flare_ID = %s", (given_flare_id,))
    records = cursor.fetchall()
    # jd_times = []  # List to store jd_time values
    # for record in records:
    #     # Assuming jd_time is the third column (index 2) in the table
    #     jd_time = record[2]
    #     jd_times.append(jd_time)
    # time1 = jd_times
    time1 = [record[2] for record in records]

    #####=========================
    cursor.execute("SELECT * FROM freq_QL_XP WHERE Flare_ID = %s", (given_flare_id,))
    records = cursor.fetchall()
    # Extract the values from the fetched records
    fghz = [record[2] for record in records]

    #####=========================
    cursor.execute("SELECT * FROM flux_QL_XP WHERE Flare_ID = %s", (given_flare_id,))
    records = cursor.fetchall()

    spec_QL = []
    # Iterate over the retrieved records and reconstruct the array
    for record in records:
        Flare_ID, Index_f, Index_t, flux = record
        while len(spec_QL) <= Index_f:
            spec_QL.append([])
        while len(spec_QL[Index_f]) <= Index_t:
            spec_QL[Index_f].append(None)
        spec_QL[Index_f][Index_t] = flux

    spec = np.array(spec_QL)

    cursor.close()
    connection.close()

    #####=========================
    from astropy.time import Time
    tim_plt_datetime = pd.to_datetime(Time(time1, format='jd').isot)
    # tim_plt_datetime = ["2021-01-01T00:00:00", "2021-01-01T00:01:00", "2021-01-01T00:02:00"]

    spec_plt_log = spec
    freq_plt = fghz

    # Create the Plotly figure
    fig = go.Figure()

    # Plot the spectral data
    # fig.add_trace(go.Scatter(x=tim_plt_datetime, y=spec_plt_log, mode='lines', name='Spectral Data')) #mode='markers'
    fig.add_trace(go.Scatter(x=tim_plt_datetime, y=spec_plt_log[0, :], mode='lines', name=f"{freq_plt[0]:.1f} GHz"))
    fig.add_trace(go.Scatter(x=tim_plt_datetime, y=spec_plt_log[1, :], mode='lines', name=f"{freq_plt[1]:.1f} GHz"))
    fig.add_trace(go.Scatter(x=tim_plt_datetime, y=spec_plt_log[2, :], mode='lines', name=f"{freq_plt[2]:.1f} GHz"))
    fig.add_trace(go.Scatter(x=tim_plt_datetime, y=spec_plt_log[3, :], mode='lines', name=f"{freq_plt[3]:.1f} GHz"))

    # Update layout
    fig.update_layout(
        title=f'Flux_XP Data for Flare ID: {flare_id}',
        xaxis_title="Time [UT]",
        yaxis_title="Flux_XP [sfu]",
        xaxis_tickformat='%H:%M:%S',
        template="plotly"  # or choose another template that fits your web design
    )

    # Convert Plotly figure to HTML
    plot_html_ID = fig.to_html(full_html=False)  # , include_plotlyjs=False
    print(f"Flare ID {flare_id}: fetch-spectral-data-xp success")

    # # Return the plot's HTML for dynamic insertion into the webpage
    plot_data_ID = json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)
    return jsonify({"plot_data_ID": plot_data_ID})




# route example
@example.route("/")
def render_example_paper():
    hostname = socket.gethostname()
    return render_template('index.html', result=[], plot_html_ID=None, hostname=hostname)
