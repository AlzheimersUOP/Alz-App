import base64

from flask import Blueprint, session, send_from_directory
from flask import redirect
from flask import render_template
from flask import request

import os
from werkzeug.utils import secure_filename

from shutil import copyfile

from .classes.preProcessClass import PreProcess
from .classes.featureReductionClass import FeatureReduction

import io

import json
import pandas as pd

from flaskr.auth import login_required, UserData
from flask import g

import numpy as np
import matplotlib.pyplot as plt

bp = Blueprint("preprocess", __name__, url_prefix="/pre")

ALLOWED_EXTENSIONS = set(['pkl', 'csv'])

from pathlib import Path

ROOT_PATH = Path.cwd()
USER_PATH = ROOT_PATH / "flaskr" / "upload" / "users"
UPLOAD_FOLDER = ROOT_PATH / "flaskr" / "upload"
ANNOTATION_TBL = UPLOAD_FOLDER / "AnnotationTbls"


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@bp.route("/")
@login_required
def index():
    list_names = []
    annotation_list = []
    path = USER_PATH / str(g.user["id"])
    if not os.path.exists(path):
        os.makedirs(path)
        os.makedirs(path / "tmp")
    for filename in os.listdir(path):
        list_names.append(filename)

    for filename in os.listdir(ANNOTATION_TBL):
        annotation_list.append(filename)

    list_names.remove("tmp")
    return render_template("preprocess/step-1.html", available_list=list_names, annotation_list=annotation_list)


# step 2 | Session > Database
@bp.route("/step-2", methods=['POST'])
@login_required
def view_merge_df():
    user_id = g.user["id"]
    annotation_table = request.form["anno_tbl"]
    col_sel_method = request.form["column_selection"]
    file_name = request.form["available_files"]

    file_path = USER_PATH / str(user_id) / file_name

    if annotation_table and col_sel_method and file_name:

        #Delete query if file already pre-processed
        UserData.delete_preprocess_file(user_id, file_name)

        #load df
        df = PreProcess.mergeDF(file_path, ANNOTATION_TBL / annotation_table)
        merge_name = "merge_" + file_name
        merge_path = USER_PATH / str(user_id) / "tmp" / merge_name
        merge_path_str = merge_path.as_posix()
        PreProcess.saveDF(df, merge_path_str)

        #save data to the Database
        UserData.add_preprocess(user_id, file_name, file_path.as_posix(), annotation_table, col_sel_method, merge_path_str)
        pre_process_id = UserData.get_user_preprocess(user_id, file_name)['id']
        session['pre_process_id'] = pre_process_id

        y = PreProcess.getDF(file_path)
        y = y['class']
        data = PreProcess.get_df_details(df, y)

        session[file_name] = data

        if len(df.columns) > 100:
            df_view = df.iloc[:, 0:100].head(15)
        else:
            df_view = df.head(15)

        return render_template("preprocess/step-2.html", tables=[df_view.to_html(classes='data')], details=data)

    return redirect('/pre')


# step 3
@bp.route("/step-3")
@login_required
def scaling_imputation():
    pre_process = g.pre_process

    if pre_process is None:
        return redirect('/pre')

    data = session.get(pre_process['file_name'])

    if data is not None:
        return render_template("preprocess/step-3.html", details=data)

    return redirect('/pre')


# normalization and null remove
@bp.route("/step-4", methods=['POST'])
@login_required
def norm():
    pre_process = g.pre_process
    user_id = pre_process['user_id']

    norm_method = request.form["norm_mthd"]
    null_rmv = request.form["null_rmv"]

    UserData.update_preprocess(user_id, pre_process['file_name'], 'scaling', norm_method)
    UserData.update_preprocess(user_id, pre_process['file_name'], 'imputation', null_rmv)

    merge_df_path = Path(pre_process['merge_df_path'])

    df = PreProcess.step3(PreProcess.getDF(merge_df_path), norm_method, null_rmv) #symbol_df

    df = PreProcess.probe2Symbol(df) #avg_symbol_df
    avg_symbol_name = "avg_symbol_" + pre_process['file_name']
    avg_symbol_df_path = USER_PATH / str(g.user["id"]) / "tmp" / avg_symbol_name

    avg_symbol_df_path_str = avg_symbol_df_path.as_posix()
    PreProcess.saveDF(df, avg_symbol_df_path_str)

    UserData.update_preprocess(user_id, pre_process['file_name'], 'avg_symbol_df_path', avg_symbol_df_path_str)

    data = session[pre_process['file_name']]
    data = PreProcess.add_details_json(data, df, "r1")
    session[pre_process['file_name']] = data

    if len(df.columns) > 100:
        df_view = df.iloc[:, 0:100].head(15)
    else:
        df_view = df.head(15)

    return render_template("preprocess/step-4.html", tablesstep4=[df_view.to_html(classes='data')],
                           details=data)


# step 4
@bp.route("/step-5")
@login_required
def feature_reduction():
    pre_process = g.pre_process

    if pre_process is None:
        return redirect('/pre')

    avg_symbol_df_path = Path(pre_process['avg_symbol_df_path'])
    file_path = Path(pre_process['file_path'])

    p_fold_df = PreProcess.get_pvalue_fold_df(avg_symbol_df_path, file_path)
    p_fold_df_path = USER_PATH / str(g.user["id"]) / 'tmp' / ('_p_fold_' + pre_process['file_name'])
    PreProcess.saveDF(p_fold_df, p_fold_df_path)

    pvalues_max = p_fold_df['pValues'].max() * 0.1
    fold_max = p_fold_df['fold'].max() * 0.2

    pvalues = np.linspace(0.001, 0.01, 19)
    pvalues = np.around(pvalues, decimals=4)
    folds = np.linspace(0.001, fold_max, 40)
    folds = np.around(folds, decimals=4)

    data_array = [pvalues, folds]

    volcano_hash = get_volcano_fig(p_fold_df['fold'], p_fold_df['pValues'])

    return render_template("preprocess/step-5.html", data_array=data_array, volcano_hash=volcano_hash)


# step 6
@bp.route("/step-6/", methods=['POST'])
@login_required
def get_reduce_features_from_pvalues():

    fold = request.form["fold-range"]
    pvalue = request.form["p-value"]

    pre_process = g.pre_process

    p_fold_df_path = USER_PATH / str(g.user["id"]) / 'tmp' / ('_p_fold_' + pre_process['file_name'])
    p_fold_df = PreProcess.getDF(p_fold_df_path)

    df = PreProcess.get_filtered_df_pvalue(p_fold_df, pre_process['avg_symbol_df_path'], float(pvalue), float(fold))
    fr_df_path = USER_PATH / str(g.user["id"]) / 'tmp' /  ('fr_' + pre_process['file_name'])
    PreProcess.saveDF(df, fr_df_path)

    length = len(df.columns)

    if (length < 350):
        split_array = np.linspace(150, int(length / 10) * 10, 21)
    else:
        split_array = np.linspace(150, 350, 21)

    split_array = split_array.astype(int)

    # Get classification Results
    df_y = PreProcess.getDF(Path(pre_process['file_path']))
    y = df_y['class']
    y = pd.to_numeric(y)

    classification_result_df = FeatureReduction.get_classification_results(df, y)
    cls_id, cls_name = FeatureReduction.get_best_cls(classification_result_df)
    UserData.update_result(g.user['id'], 'classifiers', cls_id)
    classification_result_df = classification_result_df.drop(['avg'], axis=1)

    fs_fig_hash = get_feature_selection_fig(df, df_y, length)

    UserData.update_preprocess(pre_process['user_id'], pre_process['file_name'], 'reduce_df_path', fr_df_path.as_posix() )

    return render_template("preprocess/step-6.html", split_array=split_array, fs_fig_hash=fs_fig_hash,
                           tables=[classification_result_df.to_html(classes='data')], cls_names=cls_name)


@bp.route("/fr/pf/", methods=['GET'])
@login_required
def get_feature_count_pval():

    pvalue = request.args.get("pvalue")
    foldChange = request.args.get("foldChange")

    path = USER_PATH / str(g.user["id"]) / 'tmp' / ('_p_fold_' + g.pre_process['file_name'])
    p_fold_df = PreProcess.getDF(path)

    count = PreProcess.get_filtered_df_count_pvalue(p_fold_df, float(pvalue), float(foldChange))
    return str(count)


@bp.route("/fr/save/", methods=['POST'])
@login_required
def save_reduced_df():
    features_count = request.form['features_count']

    df = PreProcess.getDF(Path(g.pre_process['reduce_df_path']))
    df_y = PreProcess.getDF(Path(g.pre_process['file_path']))
    y = df_y['class']
    y = pd.to_numeric(y)

    df_selected = FeatureReduction.getSelectedFeatures(df, int(features_count), y)

    file_name = g.pre_process['file_name']

    path = USER_PATH / str(g.user["id"]) / ('re_' + file_name)
    PreProcess.saveDF(df_selected, path)

    # UserData.update_result(user_id, 'filename', 're_' + x.file_name)

    # remove old files
    # files = ["merge_" + file_name, "avg_symbol_" + file_name, "_p_fold_"+ file_name, "fr_" + file_name]
    # folder_path = USER_PATH / str(g.user["id"]) / "tmp"
    # remove_files(folder_path, files)

    session[file_name] = ''

    return redirect('/fs/')


@bp.route('/upload')
@login_required
def upload_file_view():
    return render_template("preprocess/step-0.html")


# file upload
@bp.route('/upload/', methods=['POST'])
@login_required
def upload_file():
    file = request.files['chooseFile']

    if file and allowed_file(file.filename):

        filename = secure_filename(file.filename)
        path_csv = USER_PATH / str(g.user["id"]) / filename
        file.save(path_csv)

        name = filename.split('.')

        if name[1] == 'csv':
            path_pkl = USER_PATH / str(g.user["id"]) / (name[0] + '.pkl')
            csv2pkl(path_csv, path_pkl)
            os.remove(path_csv)

        return redirect('/pre')
    else:
        e = ["Wrong file type", ["Please upload csv file."]]
        return render_template("error.html", errors=e)


@bp.route('/sample/download/')
@login_required
def download_sample_file():
    sample_file = UPLOAD_FOLDER / "sample"

    return send_from_directory(directory=sample_file, filename='GSE5281-GPL570.zip')


@bp.route('/sample/upload/')
@login_required
def upload_sample_file():
    src = UPLOAD_FOLDER / "sample" / 'GSE5281-GPL570.zip'
    dst = USER_PATH / str(g.user['id']) / 'GSE5281-GPL570.pkl'
    # copyfile(src, dst)

    df = pd.read_csv(src)
    df = df.set_index(["ID"])
    df.index.name = None
    df.columns.name = "ID"
    df.to_pickle(dst)

    return redirect('/pre')


def csv2pkl(path_csv, path_pkl):
    df_save = pd.read_csv(path_csv)
    df_save = df_save.set_index(["ID"])
    df_save.index.name = None
    df_save.columns.name = "ID"
    df_save.to_pickle(path_pkl)
    return True


def json2df(df_name):
    if session.get(df_name):
        json_data = session[df_name]
        df = DF(**json.loads(json_data))
        return df

    return None


def df2session(obj, name):
    json_data = json.dumps(obj.__dict__)
    session[name] = json_data


def get_volcano_fig(fold_change, pValues):
    fig, axes = plt.subplots(nrows=1, ncols=1, figsize=(14, 8))
    axes.scatter(fold_change, -(np.log10(pValues)))
    axes.set_ylabel("-log10(pValue)")
    axes.set_xlabel("fold")

    pic_hash = fig_to_b64encode(fig)

    return pic_hash


def get_feature_selection_fig(df, df_y, length):
    df["class"] = df_y['class']
    selectedFeatures = FeatureReduction.getScoresFromUS(df)
    fig = FeatureReduction.create_figure(selectedFeatures, length)

    pic_hash = fig_to_b64encode(fig)

    return pic_hash


def fig_to_b64encode(fig):
    pic_IObytes = io.BytesIO()
    fig.savefig(pic_IObytes, format='png')
    pic_IObytes.seek(0)
    pic_hash = base64.b64encode(pic_IObytes.read())

    pic_hash = pic_hash.decode("utf-8")

    return pic_hash


def remove_files(path, files):
    for file in files:
        f_path = path / file
        if os.path.exists(f_path):
            os.remove(f_path)
