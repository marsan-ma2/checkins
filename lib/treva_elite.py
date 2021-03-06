import os, sys, time, pickle, gzip, re, ast
import pandas as pd
import numpy as np
import multiprocessing as mp
pool_size = mp.cpu_count()

import xgboost as xgb

from os import listdir
from datetime import datetime
from collections import OrderedDict, defaultdict

from sklearn.cross_validation import train_test_split
from sklearn.preprocessing import LabelEncoder
from sklearn.neighbors import KNeighborsClassifier
from sklearn import linear_model, ensemble

from lib import conventions as conv
from lib import submiter

#===========================================
#   Trainer
#===========================================  
class trainer(object):

  def __init__(self, params):
    self.root     = params['root']
    self.stamp    = params['stamp']
    self.size     = params['size']
    self.x_cols   = params['x_cols']
    self.x_ranges = conv.get_range(params['size'], params['x_step'], params['x_inter'])
    self.y_ranges = conv.get_range(params['size'], params['y_step'], params['y_inter'])
    # extra_info
    self.data_cache = params['data_cache']
    self.loc_th_x = params['loc_th_x']
    self.loc_th_y = params['loc_th_y']
    self.en_preprocessing = params['en_preprocessing']
    self.submit_file = "%s/submit/treva_submit_%s.csv" % (self.root, self.stamp)

    # global variable for multi-thread
    global LOCATION, AVAIL_WDAYS, AVAIL_HOURS, POPULAR, GRID_CANDS
    if os.path.exists(self.data_cache):
      LOCATION, AVAIL_WDAYS, AVAIL_HOURS, POPULAR, GRID_CANDS = pickle.load(open(self.data_cache, 'rb'))
    

  #----------------------------------------
  #   Main
  #----------------------------------------
  def train(self, df_train, df_valid, df_test):
    if self.size == 10.0:
      # mdl_path = "%s/submit/treva_full10" % (self.root)
      mdl_path = "%s/submit/treva_fast10" % (self.root)
    else:
      mdl_path = "%s/submit/treva_%s" % (self.root, self.stamp)
    if not os.path.exists(mdl_path): os.mkdir(mdl_path)
    print("[Train] start @ %s" % (conv.now('full')))
    
    processes = []
    preds_total = []
    score_stat = []
    for x_idx, (x_min, x_max) in enumerate(self.x_ranges):
      x_min, x_max = conv.trim_range(x_min, x_max, self.size)
      df_row = {
        'tr': df_train[(df_train.x >= x_min) & (df_train.x < x_max)],
        'va': df_valid[(df_valid.x >= x_min) & (df_valid.x < x_max)],
        'te': df_test[(df_test.x >= x_min) & (df_test.x < x_max)],
      }
      mp_pool = mp.Pool(pool_size)
      for y_idx, (y_min, y_max) in enumerate(self.y_ranges): 
        # check exists
        grid_submit_path = "%s/treva_%i_%i.csv" % (mdl_path, x_idx, y_idx)
        # if x_idx < 90: continue
        if os.path.exists(grid_submit_path[:-4] + '_blend.csv'):
          print("%s exists, skip." % grid_submit_path[:-4] + '_blend.csv')
          continue

        # get grid
        y_min, y_max = conv.trim_range(y_min, y_max, self.size)
        df_grid = {m: df_row[m][(df_row[m].y >= y_min) & (df_row[m].y < y_max)] for m in ['tr', 'va', 'te']}
        p = mp_pool.apply_async(drill_grid, (df_grid, self.x_cols, x_idx, y_idx, grid_submit_path))
        processes.append(p)

        # prevent memory explode!
        while (len(processes) > 30): 
          score, y_test = processes.pop(0).get()
          score_stat.append((score, len(df_grid)))
          preds_total.append(y_test)
      mp_pool.close()
    while processes: 
      score, y_test = processes.pop(0).get()
      score_stat.append((score, len(df_grid)))
      preds_total.append(y_test)
    # write submit file
    preds_total = pd.concat(preds_total)
    df2submit(preds_total, self.submit_file)
    # collect scores
    valid_score = sum([s*c for s,c in score_stat]) / sum([c for s,c in score_stat])
    print("[Treva] done, valid_score=%.4f, submit file written %s @ %s" % (valid_score, self.submit_file, conv.now('full')))
    return self.submit_file
  
#----------------------------------------
#   Drill Grid
#----------------------------------------
# KNN_NORM = {
#   'x': 500, 'y':1000, 
#   'hour':4, 'logacc':1, 'weekday':3, 
#   'qday':1, 'month':2, 'year':10, 'day':1./22,
# }

def drill_grid(df_grid, x_cols, xi, yi, grid_submit_path, do_blending=True):
  best_score = 0
  all_score = []
  Xs, ys = {}, {}
  for m in ['tr', 'te']:
    Xs[m], ys[m], row_id = conv.df2sample(df_grid[m], x_cols)
  
  # [grid search best models]
  mdl_configs = [
    {'alg': 'skrf', 'n_estimators': 500, 'max_depth': 11},
    {'alg': 'skrfp', 'n_estimators': 500, 'max_depth': 11},
    {'alg': 'sket', 'n_estimators': 500, 'max_depth': 11},
    {'alg': 'sketp', 'n_estimators': 500, 'max_depth': 11},

    # {'alg': 'skrf', 'n_estimators': 500, 'max_features': 0.35, 'max_depth': 15},
    # {'alg': 'skrfp', 'n_estimators': 500, 'max_features': 0.35, 'max_depth': 15},
    # {'alg': 'sket', 'n_estimators': 800, 'max_features': 0.5, 'max_depth': 15},
    # {'alg': 'sketp', 'n_estimators': 1000, 'max_features': 0.5, 'max_depth': 11},

    # {'alg': 'skrf', 'n_estimators': 1000, 'max_features': 0.4, 'max_depth': 9},
    # {'alg': 'skrfp', 'n_estimators': 1000, 'max_features': 0.4, 'max_depth': 9},
  ]

  # mdl_configs = []
  # for alg in ['skrf', 'skrfp', 'sket', 'sketp']:
  #   for n_estimators in [500]:
  #     for max_features in [0.5]:   # 0.4
  #       for max_depth in [15]:    # 11
  #         mdl_configs.append({'alg': alg, 'n_estimators': n_estimators, 'max_features': max_features, 'max_depth': max_depth}) 
  all_bests = []

  # for mdl_config in mdl_configs:
  #   # train
  #   clf = get_alg(mdl_config['alg'], mdl_config)
  #   clf.fit(Xs['tr'], ys['tr'])
  #   # valid
  #   score, bests = drill_eva(clf, Xs['va'], ys['va'])
  #   print("drill(%i,%i) va_score %.4f for model %s(%s) @ %s" % (xi, yi, score, mdl_config['alg'], mdl_config, conv.now('full')))
  #   if score > best_score:
  #     best_score = score
  #     best_config = mdl_config
  #   # for blending
  #   if do_blending:
  #     all_score.append(score)
  #     all_bests.append(bests)
  
  # # blending
  # if do_blending:
  #   best_bcnt = None
  #   best_blend_score = 0
  #   best_good_idxs = []
  #   for bcnt in [5, 10]:
  #     good_idxs = [k for k,v in sorted(enumerate(all_score), key=lambda v: v[1], reverse=True)][:bcnt]
  #     blended_bests = blending([m for idx, m in enumerate(all_bests) if idx in good_idxs])
  #     blended_match = [apk([ans], vals) for ans, vals in zip(ys['va'], blended_bests)]
  #     blended_score = sum(blended_match)/len(blended_match)
  #     if blended_score > best_blend_score:
  #       best_blend_score = blended_score
  #       best_good_idxs = good_idxs
  #       best_bcnt = bcnt
  #     print("drill(%i,%i) va_score %.4f for model 'blending_%i' @ %s" % (xi, yi, blended_score, bcnt, conv.now('full')))
  

  # train again with full training samples
  # Xs['tr_va'] = pd.concat([Xs['tr'], Xs['va']])
  # ys['tr_va'] = np.append(ys['tr'], ys['va'])
  

  # always write best blending (in case best single model overfitting)
  all_bt_preds = []
  # for bcfg in [m for idx,m in enumerate(mdl_configs) if idx in best_good_idxs]:
  for bcfg in mdl_configs:
    bmdl = get_alg(bcfg['alg'], bcfg)
    bmdl.fit(Xs['tr'], ys['tr'])
    _, bt_preds = drill_eva(bmdl, Xs['te'], ys['te'])
    all_bt_preds.append(bt_preds)
  blending_test_preds = blending(all_bt_preds)
  blending_test_preds = pd.DataFrame(blending_test_preds)
  blending_test_preds['row_id'] = row_id
  df2submit(blending_test_preds, (grid_submit_path[:-4] + '_blend.csv'))


  # # collect results
  # if do_blending and (best_blend_score > best_score):
  best_score = 1.0 #best_blend_score
  test_preds = blending_test_preds
  #   best_config = "blending_%i" % best_bcnt
  # else:
  #   best_model = get_alg(best_config['alg'], best_config)
  #   best_model.fit(Xs['tr_va'], ys['tr_va'])
  #   _, test_preds = drill_eva(best_model, Xs['te'], ys['te'])
  #   test_preds = pd.DataFrame(test_preds)
  #   test_preds['row_id'] = row_id

  # write partial submit  
  # df2submit(test_preds, grid_submit_path)
  # print("[drill_grid (%i,%i)] choose best_model %s, best_score=%.4f @ %s" % (xi, yi, best_config, best_score, datetime.now()))
  print("[drill_grid (%i,%i)] blended @ %s" % (xi, yi, datetime.now()))
  return best_score, test_preds


def df2submit(df, filename):
  df['place_id'] = df[[0,1,2]].astype(str).apply(lambda x: ' '.join(x), axis=1)
  df[['row_id', 'place_id']].sort_values(by='row_id').to_csv(filename, index=False)


def blending(all_bests, rank_w=[1, 0.6, 0.4]):
  blended_bests = []
  for i in range(len(all_bests[0])):
    stat = defaultdict(float)
    for line in [m[i] for m in all_bests]:
      for c, s in enumerate(line):
        stat[s] += rank_w[c]
    stat = sorted(stat.items(), key=lambda v: v[1], reverse=True)
    stat = [pid for pid,val in stat][:3]
    blended_bests.append(stat)
  return blended_bests


def apk(actual, predicted, k=3):
  if len(predicted) > k: 
    predicted = predicted[:k]
  score, num_hits = 0.0, 0.0
  for i,p in enumerate(predicted):
    if p in actual and p not in predicted[:i]:
      num_hits += 1.0
      score += num_hits / (i+1.0)
  if not actual: return 0.0
  return score / min(len(actual), k)


def drill_eva(clf, X, y, time_th_wd=0.003, time_th_hr=0.004):
  final_bests = []
  sols = clf.predict_proba(X)
  sols = [[(clf.classes_[i], v) for i, v in enumerate(line)] for line in sols]
  for i in range(len(X)):
    psol = OrderedDict(sorted(sols[i], key=lambda v: v[1]))
    # -----[filter avail places]-----
    # s = X.iloc[i]
    # psol = {p: v * (
    #     0.1 * (AVAIL_WDAYS.get((p, s.weekday.astype(int)), 0) > time_th_wd) + 
    #     0.4 * (AVAIL_HOURS.get((p, s.hour.astype(int)), 0) > time_th_hr)
    # ) for p,v in psol.items()}
    psol = sorted(list(psol.items()), key=lambda v: v[1], reverse=True)
    psol = [p for p,v in psol]
    final_bests.append(psol[:3])
  if y is not None:
    match = [apk([ans], vals) for ans, vals in zip(y, final_bests)]
    score = sum(match)/len(match)
  else: 
    score = None
  return score, final_bests


def get_alg(alg, mdl_config):
  if alg == 'skrf':
    clf = ensemble.RandomForestClassifier(
      n_estimators=mdl_config.get('n_estimators', 500), 
      max_features=mdl_config.get('max_features', 0.35),  
      max_depth=mdl_config.get('max_depth', 15), 
      n_jobs=-1,
    )
  elif alg == 'skrfp':
    clf = ensemble.RandomForestClassifier(
      n_estimators=mdl_config.get('n_estimators', 500), 
      max_features=mdl_config.get('max_features', 0.35),
      max_depth=mdl_config.get('max_depth', 15), 
      criterion='entropy',
      n_jobs=-1,
    )
  elif alg =='sket':
    clf = ensemble.ExtraTreesClassifier(
      n_estimators=mdl_config.get('n_estimators', 500), 
      max_features=mdl_config.get('max_features', 0.5),  
      max_depth=mdl_config.get('max_depth', 15), 
      n_jobs=-1,
    )
  elif alg =='sketp':
    clf = ensemble.ExtraTreesClassifier(
      n_estimators=mdl_config.get('n_estimators', 500), 
      max_features=mdl_config.get('max_features', 0.5),  
      max_depth=mdl_config.get('max_depth', 11), 
      criterion='entropy', 
      n_jobs=-1,
    )
  elif alg == 'skgbc':
    clf = ensemble.GradientBoostingClassifier(
      n_estimators=mdl_config.get('n_estimators', 30), 
      max_depth=mdl_config.get('max_depth', 5)
    )
  elif alg == 'xgb':
    # https://github.com/dmlc/xgboost/blob/master/python-package/xgboost/sklearn.py
    clf = xgb.XGBClassifier(
      n_estimators=mdl_config.get('n_estimators', 30), 
      max_depth=mdl_config.get('max_depth', 7), 
      learning_rate=mdl_config.get('learning_rate', 0.1), 
      objective="multi:softprob", 
      silent=True
    )
  elif alg == 'knn':
    clf = KNeighborsClassifier(n_neighbors=25, weights='distance', metric='manhattan', n_jobs=-1)
  elif alg == 'sklr':
    clf = linear_model.LogisticRegression(multi_class='multinomial', solver = 'lbfgs')
  return clf


#===========================================
#   Analyse model parameter from log
#===========================================
# submit_partial_merge(base="blending_20160621_214954_0.58657.csv.gz", folder="treva_full10")
def submit_partial_merge(base, folder, all_blended=False):
  root_path = '/home/workspace/checkins'
  folder = "%s/submit/%s" % (root_path, folder)
  stamp = str(datetime.now().strftime("%Y%m%d_%H%M%S"))
  output = "%s/submit/treva_overwrite_%s_all_blended_%s.csv" % (root_path, stamp, all_blended)

  if all_blended:
    tfiles = [f for f in listdir(folder) if 'blend' in f]
  else:
    tfiles = [f for f in listdir(folder) if 'blend' not in f]

  # # remove old batch
  # print("tfiles before removing old batch: %i" % len(tfiles))
  # old_partials = [f for f in listdir(root_path + "/submit/treva_merge")]
  # tfiles = [f for f in tfiles if f not in old_partials]
  # print("tfiles after removing old batch: %i" % len(tfiles))

  # concat and merge
  df_treva = [pd.read_csv("%s/%s" % (folder, f)) for f in tfiles]
  df_treva = pd.concat(df_treva).sort_values(by='row_id')
  df_base = pd.read_csv("%s/data/submits/%s" % (root_path, base))

  df_base = df_base[~df_base.row_id.isin(df_treva.row_id.values)]
  df_overwrite = pd.concat([df_base, df_treva]).sort_values(by='row_id')
  df_overwrite[['row_id', 'place_id']].sort_values(by='row_id').to_csv(output, index=False)
  print("ensure dim:", len(df_treva), len(set(df_treva.row_id.values)), len(set(df_overwrite.row_id.values)))
  print("overwrite output written in %s @ %s" % (output, datetime.now()))
  # submiter.submiter().submit(entry=output, message="treva submit_partial_merge with %s and all_blended=%s" % (base, all_blended))


def submit_cumulation(folder, all_blended=False):
  root_path = '/home/workspace/checkins'
  folder = "%s/submit/%s" % (root_path, folder)
  stamp = str(datetime.now().strftime("%Y%m%d_%H%M%S"))
  output = "%s/submit/treva_cumulate_%s_all_blended_%s.csv" % (root_path, stamp, all_blended)

  if all_blended:
    tfiles = [f for f in listdir(folder) if 'blend' in f]
  else:
    tfiles = [f for f in listdir(folder) if 'blend' not in f]

  # concat and merge
  df_treva = [pd.read_csv("%s/%s" % (folder, f)) for f in tfiles]
  df_treva = pd.concat(df_treva).sort_values(by='row_id')
  # df_base = pd.read_csv("%s/data/submits/%s" % (root_path, base))

  # df_base = df_base[~df_base.row_id.isin(df_treva.row_id.values)]
  # df_overwrite =  pd.concat([df_base, df_treva]).sort_values(by='row_id')
  df_treva[['row_id', 'place_id']].sort_values(by='row_id').to_csv(output, index=False)
  # print("ensure dim:", len(df_treva), len(set(df_treva.row_id.values)), len(set(df_overwrite.row_id.values)))
  print("overwrite output written in %s @ %s" % (output, datetime.now()))


def analysis_params(log_path):
  raw = open(log_path, 'rt')
  cfg_stat = defaultdict(float)
  for line in raw.readlines():
    if 'va_score' in line:
      if 'blending' in line:
        cfg = 'blending'
      else:
        cfg = re.compile('{.*}').findall(line)[0]
      score = float(re.compile('0\.\d+').findall(line)[0])
      # print(score, cfg)
      cfg_stat[cfg] += score
  results = sorted(cfg_stat.items(), key=lambda v: v[1], reverse=True)
  for line in results:
    print(line)

def analysis_best(log_path):
  raw = open(log_path, 'rt')
  cfg_stat = defaultdict(float)
  for line in raw.readlines():
    if 'model is' in line:
      if 'blending' in line:
        cfg = 'blending'
      else:
        cfg = re.compile('{.*}').findall(line)[0]
      cfg_stat[cfg] += 1
  results = sorted(cfg_stat.items(), key=lambda v: v[1], reverse=True)
  for line in results:
    print(line)



if __name__ == '__main__':
    # -----[analyses treva params]-----
    log_path = "/home/workspace/checkins/logs/nohup_treva_all_20160626_090148.log"
    analysis_params(log_path)
    # analysis_best(log_path)

    # # -----[submit treva partial merge]-----
    # submit_partial_merge(base='blending_20160621_214954_0.58657.csv.gz', folder="treva_merge2", all_blended=False)
    # submit_partial_merge(base='blending_20160621_214954_0.58657.csv.gz', folder="treva_merge2", all_blended=True)
