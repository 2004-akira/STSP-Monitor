from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.http import MediaIoBaseDownload
from datetime import datetime
import pandas as pd
import io
import pytz
import requests
import json
import os

now = datetime.now(pytz.timezone('Asia/Tokyo'))

FOLDER_ID = os.environ["FOLDER_ID"]
TOKEN = os.environ["STSP_TOKEN"]
schoolclass = os.environ["SCHOOLCLASS"]

SCOPES = [
    "https://www.googleapis.com/auth/drive"
]

service_account_info = json.loads(
    os.environ["GOOGLE_SERVICE_ACCOUNT"]
)

credentials = (
    service_account.Credentials
    .from_service_account_info(
        service_account_info,
        scopes=SCOPES
    )
)

drive_service = build(
    "drive",
    "v3",
    credentials=credentials
)

html_filename = (
    f"2026年度_{schoolclass}_スタサプ提出状況.html"
)

URL = "https://learn.studysapuri.jp/qlearn/v1/timeline_activities"

headers = {
    "Authorization": f"Token {TOKEN}",
    "Accept": "application/json"
}

response = requests.get(URL, headers=headers)

response.raise_for_status()

activities = response.json()

rows = []

for item in activities:

    last_name = item["owner"]["last_name"]
    first_name = item["owner"]["first_name"]
    student_name = f"{last_name} {first_name}"

    subject = item["trackable"]["name"]

    is_mastered = (
        " ◎"
        if item["key"] == "activity.topic.mastered"
        else ""
    )

    submitted_dt = datetime.fromtimestamp(
        item["created_ts"],
        tz=pytz.timezone("Asia/Tokyo")
    )

    submitted_at = submitted_dt.strftime("%-m/%-d %H:%M:%S")

    rows.append({
        "ID": item["id"],
        "生徒氏名": student_name,
        "学習内容": subject,
        "全問正解": is_mastered,
        "提出日時": submitted_at,
        "タイムスタンプ": submitted_dt
    })

df = pd.DataFrame(rows)

record_start = pd.Timestamp(
    "2026-06-13 12:00:00",
    tz="Asia/Tokyo"
)

df = df[
    df["タイムスタンプ"] >= record_start
]

df["記録"] = (
    df["提出日時"] + df["全問正解"]
)

results = (
    drive_service.files()
    .list(
        q=f"'{FOLDER_ID}' in parents and trashed=false",
        fields="files(id,name)"
    )
    .execute()
)

file_list = results["files"]

history_drive_file = None
status_drive_file = None
html_drive_file = None

STATUS_FILE = "status.json"

for file in file_list:

    if file["name"] == "activity_history.csv":

        history_drive_file = file

        request = (
            drive_service.files()
            .get_media(fileId=file["id"])
        )

        fh = io.FileIO(
            "activity_history.csv",
            "wb"
        )

        downloader = MediaIoBaseDownload(
            fh,
            request
        )

        done = False

        while not done:

            status, done = downloader.next_chunk()

    elif file["name"] == "status.json":

        status_drive_file = file

        request = (
            drive_service.files()
            .get_media(fileId=file["id"])
        )

        fh = io.FileIO(
            "status.json",
            "wb"
        )

        downloader = MediaIoBaseDownload(
            fh,
            request
        )

        done = False

        while not done:

            status_progress, done = (
                downloader.next_chunk()
            )

    elif file["name"] == html_filename:

        html_drive_file = file

if history_drive_file is None:

    raise Exception(
        "「activity_history.csv」がDrive上に存在しません。"
    )

if status_drive_file is None:

    raise Exception(
        "「status.json」がDrive上に存在しません。"
    )

if html_drive_file is None:

    raise Exception(
        f"「{html_filename}」がDrive上に存在しません。"
    )

with open(
    STATUS_FILE,
    "r",
    encoding="utf-8"
) as f:

    status = json.load(f)

history_file = "activity_history.csv"

history_df = pd.read_csv(
    history_file,
    encoding="utf-8-sig"
)

history_df["ID"] = history_df["ID"].astype(str)
df["ID"] = df["ID"].astype(str)

new_df = df[
    ~df["ID"].isin(history_df["ID"])
]

history_df = pd.concat(
    [history_df, new_df],
    ignore_index=True
)

history_df["タイムスタンプ"] = pd.to_datetime(
    history_df["タイムスタンプ"],
    utc=True
).dt.tz_convert("Asia/Tokyo")

history_df["全問正解"] = (
    history_df["全問正解"]
    .fillna("")
)

today = now.date()

yesterday = (
    now - pd.Timedelta(days=1)
).date()

def format_display(row):

    d = row["タイムスタンプ"].date()

    time_part = row["タイムスタンプ"].strftime(
        "%H:%M:%S"
    )

    if d == today:

        date_part = "今日"

    elif d == yesterday:

        date_part = "昨日"

    else:

        date_part = row["タイムスタンプ"].strftime(
            "%-m/%-d"
        )

    return (
        f"{date_part} {time_part}"
        f"{row['全問正解']}"
    )

history_df["記録"] = (
    history_df.apply(
        format_display,
        axis=1
    )
)

history_df = history_df.drop_duplicates(
    subset=["ID"]
)

history_df = history_df.sort_values(
    "タイムスタンプ",
    ascending=False
)

history_df.to_csv(
    history_file,
    index=False,
    encoding="utf-8-sig"
)

media = MediaFileUpload(
    history_file,
    resumable=True
)

drive_service.files().update(
    fileId=history_drive_file["id"],
    media_body=media
).execute()

if len(df) == 40 and len(new_df) == 40:

    status["history_incomplete"] = True

    status["overflow_detected_at"].append(
        now.strftime("%-m/%-d %H:%M")
    )

    with open(
        STATUS_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            status,
            f,
            ensure_ascii=False,
            indent=4
        )

media = MediaFileUpload(
    "status.json",
    resumable=True
)

drive_service.files().update(
    fileId=status_drive_file["id"],
    media_body=media
).execute()

summary = (
    history_df.groupby(["学習内容", "生徒氏名"])["記録"]
      .apply(lambda x: "<br>".join(x))
      .reset_index()
)

pivot = summary.pivot(
    index="学習内容",
    columns="生徒氏名",
    values="記録"
)

pivot = pivot.fillna("")

submitted_count = (
    history_df.groupby("学習内容")["生徒氏名"]
      .nunique()
)

stats = pd.DataFrame({
    "提出人数": submitted_count
})

stats["提出人数"] = (
    stats["提出人数"]
        .astype(int)
        .astype(str)
        + "人"
)

result = pivot.join(stats)

latest_time = (
    history_df.groupby("学習内容")["タイムスタンプ"]
      .max()
)

result["最新提出日時"] = latest_time

result = result.sort_values(
    "最新提出日時",
    ascending=False
)

result = result.drop(
    columns=["最新提出日時"]
)

result = result[
    ["提出人数"]
    + [c for c in result.columns
       if c != "提出人数"]
]

result = result.reset_index()

result["学習内容"] = (
    '<div class="subject">'
    + result["学習内容"].astype(str)
    + '</div>'
)

latest_logs = (
    history_df.sort_values(
        "タイムスタンプ",
        ascending=False
    )
    .drop_duplicates(
        subset=["生徒氏名"]
    )
)

total_students = (
    history_df["生徒氏名"]
    .nunique()
)

latest_row = {
    "学習内容": "最新の提出記録",
    "提出人数": f"{total_students}人"
}

for _, row in latest_logs.iterrows():

    latest_row[row["生徒氏名"]] = (
        f'<div class="latest-row">'
        f'{row["記録"]}<br>'
        f'{row["学習内容"]}'
        f'</div>'
    )

result = pd.concat(
    [
        pd.DataFrame([latest_row]),
        result
    ],
    ignore_index=True
)

html = result.to_html(
    escape=False,
    na_rep="",
    border=1,
    index=False
)

html = html.replace(
    "<tbody>\n    <tr>",
    '<tbody>\n    <tr class="latest-row">',
    1
)

periodstart = record_start.strftime("%-m/%-d %H:%M")
periodend = now.strftime("%-m/%-d %H:%M")
incomplete = ""

if status["history_incomplete"]:

    incomplete = (
        "<br>"
        "※ 提出された宿題の一部が記録されていないことがあります。"
    )

with open(
    html_filename,
    "w",
    encoding="utf-8"
) as f:
    f.write(f"""
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>2026年度_{schoolclass}_スタサプ提出状況_{now.strftime("%m%d%H%M")}</title>

<style>
body {{
    font-family: sans-serif;
    font-size: 16px;
    line-height: 1.2;
}}

.site-header {{
  background-color: #622599;
  color: #ffffff;
  font-size: 16px;
  display: flex;
  justify-content: center;
  align-items: center;
  width: 100%;
  height: 70px;
}}

.description {{
  font-size: 12px;
  color: #00664b;
  text-align: center;
}}

.table-container {{
    overflow: auto;
    max-height: 85vh;
}}

table {{
    border-collapse: collapse;
}}

th, td {{
    border: 1px solid #000000;
    padding: 3px;
    vertical-align: top;
    text-align: left;
}}

th {{
    background-color: #808080;
    color: #ffffff;
    position: sticky;
    top: 0;
    z-index: 30;
}}

th, td {{
    white-space: normal;
    overflow-wrap: break-word;
}}

table th:nth-child(1),
table td:nth-child(1) {{
    width: 250px;
    min-width: 250px;
    max-width: 250px;
    position: sticky;
    left: 0;
    z-index: 20;
    background-color: #ffffee;
}}

table th:nth-child(2),
table td:nth-child(2) {{
    width: 70px;
    min-width: 70px;
    max-width: 70px;
}}

table th:nth-child(n+3),
table td:nth-child(n+3) {{
    width: 180px;
    min-width: 180px;
    max-width: 180px;
}}

table th:nth-child(1) {{
    z-index: 40;
    background-color: #808080;
    color: #ffffff;
}}

.latest-row td {{
    background-color: #eeffff !important;
}}

.latest-row td:nth-child(1) {{
    background-color: #eeffff !important;
}}

.subject {{
    background-color: #ffffee;
}}
</style>

</head>
<body>

<header class="site-header">
  <h1>2026年度 {schoolclass} スタサプ提出状況</h1>
</header>
<h3 class="description">記録開始： {periodstart}   ・   最終更新： {periodend}<br>
<span style="font-size: 10px;">記録開始から最終更新までの期間に提出されたスタサプの宿題の提出日時を表示します。<br>
全問正解したら提出日時のとなりに「◎」がつきます。{incomplete}</span></h3>

<div class="table-container">
{html}
</div>

</body>
</html>
""")

media = MediaFileUpload(
    html_filename,
    resumable=True
)

drive_service.files().update(
    fileId=html_drive_file["id"],
    media_body=media
).execute()
