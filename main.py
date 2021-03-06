import os, sys, time, pickle, gzip, operator
import pandas as pd
import numpy as np
import multiprocessing as mp
pool_size = mp.cpu_count()

from random import random
from datetime import datetime
from collections import Counter

from lib import conventions as conv
from lib import evaluator, parser, trainer, submiter, tuner
from lib import treva_cv as treva
# from lib import treva_elite as treva
# from lib import treva as treva
from lib import community_feats as cfeats

#===========================================
#   Main Flow
#===========================================
class main(object):

  def __init__(self, root='.', params={}):
    self.timestamp = str(datetime.now().strftime("%Y%m%d_%H%M%S"))
    self.all_feats = ['hour', 'qday', 'weekday', 'month', 'year', 'logacc', 'x', 'y'] #, 'tsne_x', 'tsne_y', 'kmeans']
    # self.all_feats = ['p720', 'p1260', 'p1440', 'p1680', 'monthday', 'hour', 'qday', 'weekday', 'month', 'year', 'logacc', 'x', 'y']
    self.params = {
      'root'            : root,
      'x_cols'          : self.all_feats,
      #-----[best parameters]-----
      'x_step'          : 0.2,
      'y_step'          : 0.1,
      'x_inter'         : 1,
      'y_inter'         : 1,
      #-----[data engineering in parser]-----
      'train_test_split_time'   : 655200,   # confirmed!
      # 'train_test_split_time'   : 700000,   # confirmed!
      'place_min_checkin'       : 5, #3,
      # 'place_min_last_checkin'  : 0, #600000,   # for submit  20160605_071204_0.6454
      # 'train_min_time'          : 300000,   # for submit (not good, no use!)
      # 'place_max_first_checkin' : 300000,   # for valid only, not for submit!
      # 'train_max_time'          : 500000,   # for valid only, not for submit!
      'remove_distance_outlier' : True,
      #-----[pre-processing]-----
      'en_preprocessing'        : 0, #'HW',  # 'XYWHP'
      'max_cands'               : 10,
      #-----[post-processing]-----
      'mdl_weights'             : (0, 1, 0),  # good, could probe further!
      'time_th_wd'              : 0.003,
      'time_th_hr'              : 0.004,
      'popu_th'                 : 0.005,
      'loc_th_x'                : 3,
      'loc_th_y'                : 2,
    }
    for k,v in params.items(): self.params[k] = v   # overwrite if setup
    for f in ['logs', 'models', 'data/cache', 'submit', 'valid']:
      op_path = '%s/%s' % (self.params['root'], f)
      if not os.path.exists(op_path): os.mkdir(op_path)


  def cmd_parse(self, argv):
    if (len(argv) < 3): 
      print(''' 
        [Usage] 
          1. train model: 
            python3 main.py skrf 1.0 0.05
          2. compare all models
            python3 main.py all 1.0 0.05
      ''')
      sys.exit()
    cmds = {idx: c for idx, c in enumerate(sys.argv)}
    self.params['alg']    = cmds.get(1)
    self.params['size']   = float(cmds.get(2))
    self.params['stamp']  = cmds.get(3)

    

  def init_team(self):
    # parser & preprocessing
    self.params['stamp'] = self.params.get('stamp') or "%s_%s" % (self.params['alg'], self.timestamp)
    self.params['data_cache'] = "%s/data/cache/data_cache_size%.2f_split%i_x%.2fy%.2f.pkl" % (self.params['root'], self.params['size'], self.params['train_test_split_time'], self.params['x_step'], self.params['y_step'])
    self.pas = parser.parser(self.params)
    if not os.path.exists(self.params['data_cache']):
      df_train, df_valid, _ = self.pas.get_data()
      # self.pas.init_data_cache(pd.concat([df_train, df_valid]), self.params)
      self.pas.init_data_cache(df_train, self.params)
    # workers
    self.tra = trainer.trainer(self.params)
    self.eva = evaluator.evaluator(self.params)
    print("=====[Start] @ %s=====" % (datetime.now()))
    for k, v in self.params.items(): print("%s = %s" % (k,v))
    print("="*50)

    
  def run_cfeats(self):
    cfeats.main(params={
      'alg': self.params['alg'],
      'stamp': self.params['stamp'],
      'cv': ("cv" in self.params['stamp']),
    })


  def run(self):
    run_cmd = self.params['alg']
    alg = run_cmd.split('_')[0]
    print("[RUN_CMD] %s" % run_cmd)
    start_time = time.time()
    #==========================================
    #   Shared config
    #==========================================
    if 'knn' in run_cmd:
      self.params['norm'] = {
        'x': 500, 'y':1000, 
        'hour':4, 'logacc':1, 'weekday':3, 
        'qday':1, 'month':2, 'year':10, 'day':1./22,
      }
      # self.params['norm'] = {'x': 700, 'y':1100, 'hour':4, 'qday':1, 'logacc':1, 'weekday':3, 'day':1./22., 'month':2, 'year':10}
      self.params['x_cols'] = [x for x in self.params['x_cols'] if x in self.params['norm'].keys()]
      self.params['x_step'] = 0.25
      self.params['y_step'] = 0.25

    if 'try_inter' in run_cmd:
      self.params['x_inter'] = 2
      self.params['y_inter'] = 2
      self.params['mdl_weights'] = (0.4, 1, 0.4)

    if 'try_large_grid' in run_cmd:
      self.params['x_step'] = 0.4
      self.params['y_step'] = 0.4

    #==========================================
    #   Choose-one config
    #==========================================
    if run_cmd == 'all':
      for a in ['skrf', 'skrfp', 'sket', 'sketp', 'knn', 'xgb', 'skgbc']:
        self.init_team()
        self.train_alg(a)
    #------------------------------------------
    elif 'skrf_reverse_valid_split_time' in run_cmd:
      self.params['train_test_split_time'] = 100000
      self.params['place_min_last_checkin'] = None
      self.init_team()
      self.train_alg(alg)
    #------------------------------------------
    elif '_grid_step' in run_cmd:
      # for x_step in [0.04, 0.05, 0.08, 0.1, 0.2]:
      #   for y_step in [0.04, 0.05, 0.08, 0.1, 0.2]:
      for x_step in [0.1, 0.2, 0.5, 1]:
        for y_step in [0.1, 0.2, 0.5, 1]:
          print("=====[%s for step=(%.2f, %.2f)]=====" % (run_cmd, x_step, y_step))
          self.params['x_step'] = x_step
          self.params['y_step'] = y_step
          self.init_team()
          self.train_alg(alg)
    #------------------------------------------
    elif run_cmd == 'knn_grid_weights':
      # self.params['norm'] = {'x': 500, 'y':1000, 'hour':4, 'qday':1, 'logacc':1, 'weekday':3, 'day':1./22., 'month':2, 'year':10}
      for logacc in np.arange(1, 10, 2):
        for qday in np.arange(1, 10, 2):   
          print("[knn_grid_weights] logacc=%i, qday=%i" % (logacc, qday))
          self.params['norm']['logacc'] = logacc
          self.params['norm']['qday'] = qday
          self.init_team()
          self.train_alg(alg)
    #------------------------------------------
    elif run_cmd == 'skrf_recursive_feature_elimination':
      fixed_feats = {'logacc', 'qday', 'x', 'y', 'hour', 'weekday', 'year', 'month'}
      feats = set(self.all_feats)
      print("[RFE] checking x_cols for %s" % (feats - fixed_feats))
      while True:
        scores = {}
        self.params['x_cols'] = list(feats)
        self.init_team()
        scores['all'] = self.train_alg(alg)
        print("[RFE] baseline = %.4f" % scores['all'])
        for af in (feats - fixed_feats):
          self.params['x_cols'] = [a for a in feats if a != af]
          self.init_team()
          print("[RFE] x_cols remove [%s], using %s" % (af, self.params['x_cols']))
          scores[af] = self.train_alg(alg)
        rm_feat, rm_score = sorted(scores.items(), key=operator.itemgetter(1), reverse=True)[0]
        if rm_score > scores['all'] - 0.01: 
          print("[RFE] base_score = %.4f, remove %s to achieve %.4f" % (scores['all'], rm_feat, rm_score))
          feats -= set([rm_feat])
        else:
          print("[RFE] finished since no feature shall be removed!")
          break
    #------------------------------------------
    elif 'skrf_mdl_weights' in run_cmd:
      for sw in np.arange(0, 1.2, 0.1):
        self.params['mdl_weights'] = (sw, 0, 1.0, 0, sw)
        self.init_team()
        self.train_alg(alg)
    #------------------------------------------
    elif 'skrf_preprocessing' in run_cmd:
      for en in [0, 1]:
        self.params['en_preprocessing'] = en
        self.init_team()
        self.train_alg(alg)
    #------------------------------------------
    elif 'skrf_max_cands' in run_cmd:
      for proc in ['W', 'H']:
        for cants in np.arange(10, 50, 10):
          self.params['en_preprocessing'] = proc
          self.params['max_cands'] = cants
          self.init_team()
          self.train_alg(alg)
    #------------------------------------------
    elif 'skrf_remove_distance_outlier' in run_cmd:
      for std in np.arange(1, 3, 0.5):
        self.params['remove_distance_outlier'] = std
        self.init_team()
        self.train_alg(alg)
    #------------------------------------------
    elif run_cmd == 'skrf_feats_sel':
      all_feats = self.all_feats
      # baseline
      self.params['x_cols'] = all_feats
      self.init_team()
      self.train_alg(alg)
      # drop 1 feature
      for af in all_feats:
        self.params['x_cols'] = [a for a in all_feats if a != af]
        self.init_team()
        self.train_alg(alg)
    #------------------------------------------
    elif run_cmd == 'skrf_gs_time':
      for mfc in [None, 200000, 250000, 300000]:
        for tmt in [None]: #, 400000, 500000, 600000, 700000]:
          self.params['place_max_first_checkin'] = mfc
          self.params['train_max_time'] = tmt
          self.init_team()
          self.train_alg(alg)
    #------------------------------------------
    elif run_cmd == 'skrf_gs_loc_th':
      # for th_y in np.arange(1.5, 2.5, 0.1):
      #   for th_x in np.arange(0.6, 2, 0.2):
      for th_y in np.arange(1.7, 2.5, 0.2):
        for th_x in np.arange(2.3, 3.5, 0.2):
          print("[SKRF_GS_LOC_TH]: th_x=%s, th_y=%s" % (th_x, th_y))
          self.params['loc_th_x'] = th_x
          self.params['loc_th_y'] = th_y
          self.init_team()
          self.evaluate_model(evaluate=True, submit=False)
    #------------------------------------------
    elif run_cmd == 'skrf_place_min_checkin':
      for mc in np.arange(0, 5, 1):
        self.params['place_min_checkin'] = mc
        self.init_team()
        self.train_alg(alg)
    #------------------------------------------
    elif run_cmd == 'skrf_gs_time_th_wd':
      for pth in np.arange(0, 0.005, 0.001):
        self.params['time_th_wd'] = pth
        self.init_team()
        self.evaluate_model(evaluate=True, submit=False)
    #------------------------------------------
    elif run_cmd == 'skrf_gs_time_th_hr':
      for pth in np.arange(0.005, 0.02, 0.002):
        self.params['time_th_hr'] = pth
        self.init_team()
        self.train_alg(alg)
    #------------------------------------------
    elif run_cmd == 'skrf_gs_popu_th':
      for pth in np.arange(0, 0.005, 0.001):
        self.params['popu_th'] = pth
        self.init_team()
        self.evaluate_model(evaluate=True, submit=False)
    #------------------------------------------
    elif run_cmd in ['skrf_gs_params', 'skrfp_gs_params']:
      self.init_team()
      for max_feats in [0.3, 0.35, 0.4]:
        for n_estimators in [500]:
          for max_depth in [15]:
            self.train_alg(alg, mdl_config={'n_estimators': n_estimators, 'max_depth': max_depth, 'max_features': max_feats})
    elif run_cmd in ['sket_gs_params', 'sketp_gs_params']:
      self.init_team()
      for n_estimators in [800, 1200, 1500]:
        for max_depth in [13, 15, 18]:
          for max_feats in ['auto', 0.4, 0.5, 0.6]:
            self.train_alg(alg, mdl_config={'n_estimators': n_estimators, 'max_depth': max_depth, 'max_features': max_feats})
    elif run_cmd == 'xgb_gs_params':
      self.init_team()
      for n_estimators in [30, 35, 40]:
        for max_depth in [3, 4, 5]:
          for learning_rate in [0.1]:
            self.train_alg(alg, params={'n_estimators': n_estimators, 'max_depth': max_depth, 'learning_rate': learning_rate})
    #------------------------------------------
    elif run_cmd == 'skrf_place_min_last_checkin':
      for mlc in [550000, 650000]:
        self.params['place_min_last_checkin'] = mlc
        self.params['stamp'] = "%s_%s_%i" % (self.params['alg'], self.timestamp, mlc/1e4)
        self.init_team()
        self.train_alg(alg, submit=True)
    #------------------------------------------
    elif run_cmd == 'skrf_train_min_time':
      for mlc in [0, 50000, 100000, 150000, 200000]:
        self.params['train_min_time'] = mlc
        self.params['stamp'] = "%s_%s_%i" % (self.params['alg'], self.timestamp, mlc/1e4)
        self.init_team()
        self.train_alg(alg, submit=True)
    #------------------------------------------
    elif 'submit_rf_family' in run_cmd:
      self.params['train_test_split_time'] = 1e10   # use all samples for training
      self.init_team()
      for a in ['skrf', 'skrfp']:
        self.train_alg(a, keep_model=True, submit=True, upload=True)
      self.train_alg('knn', submit=True, upload=True)
    elif 'submit_et_family' in run_cmd:
      self.params['train_test_split_time'] = 1e10   # use all samples for training
      self.init_team()
      for a in ['sket', 'sketp']:
        self.train_alg(a, submit=True, upload=True)
    elif 'submit_full' in run_cmd:
      self.params['train_test_split_time'] = 1e10   # use all samples for training
      self.init_team()
      self.train_alg(alg, submit=True, upload=True)
    elif '_submit' in run_cmd:
      self.init_team()
      self.train_alg(alg, keep_model=True, submit=True)
    elif 'eva_exist' in run_cmd:
      self.init_team()
      self.evaluate_model(evaluate=True, submit=False)
    elif 'smt_exist' in run_cmd:
      self.params['train_test_split_time'] = 1e10
      self.init_team()
      self.evaluate_model(evaluate=False, submit=True, upload=True)
    #------------------------------------------
    elif 'fast' in run_cmd: # fast flow debug
      self.init_team()
      self.train_alg(alg, mdl_config={'n_estimators': 5})
    #------------------------------------------
    elif run_cmd == 'treva_cv':
      self.init_team()
      df_train, df_valid, df_test = self.pas.get_data()
      tva = treva.trainer(self.params)
      tva.train(df_train, df_valid, df_test)
    #------------------------------------------
    elif 'treva' in run_cmd:
      if 'elite' in run_cmd:
        self.params['train_test_split_time'] = 1e10
      else:
        self.params['train_test_split_time'] = 700000
      self.init_team()
      df_train, df_valid, df_test = self.pas.get_data()
      tva = treva.trainer(self.params)
      sfile = tva.train(df_train, df_valid, df_test)
      submiter.submiter().submit(entry=sfile, message=self.params)
    elif run_cmd == 'tuner':
      self.init_team()
      df_train, df_valid, _ = self.pas.get_data()
      grids = []
      for i in range(10):
        xb, yb = int(125*random())*0.08, int(125*random())*0.08
        grids += [(xb, xb+0.08, yb, yb+0.08)]
      print(grids)
      df_all = pd.concat([df_train, df_valid])
      all_scores = tuner.tuner(df_all, grids)
    else: # single model
      self.init_team()
      self.train_alg(alg)
    #------------------------------------------
    print("[Finished!] Elapsed time overall for %.2f secs" % (time.time() - start_time))


  #----------------------------------------
  #   Main
  #----------------------------------------
  def train_alg(self, alg, keep_model=False, submit=False, upload=False, mdl_config={}):
    # get data
    start_time = time.time()
    norm = self.params.get('norm')
    df_train, df_valid, df_test = self.pas.get_data()
      
    # train & test
    print("[train_alg]: alg=%s, mdl_config=%s" % (alg, mdl_config))
    self.tra.train(df_train, alg=alg, mdl_config=mdl_config, norm=norm)
    train_score, valid_score = 0, 0
    if self.params['size'] <= 0.5:  # eva.train only when dev.
      _, train_score = self.eva.evaluate(df_train, title='Eva.Train', norm=norm)
    if len(df_valid) > 0:
      valids_total, valid_score = self.eva.evaluate(df_valid, title='Eva.Test', norm=norm)
      pickle.dump([valids_total, df_valid], open("%s/valid/valid_%s.pkl" % (self.params['root'], self.params['stamp']), 'wb'))
      # self.eva.gen_submit_file(valids_total, valid_score, title='valid')
    
    if alg in ['skrf', 'skrfp', 'sket', 'sketp']: 
      print("[skrf feature_importance]", self.get_feature_importance())

    # save & clear
    if not keep_model:
      self.eva.clear_meta_files()
    if submit:
      preds_total, _ = self.eva.evaluate(df_test, title='Submit', norm=norm)
      sfile = self.eva.gen_submit_file(preds_total, valid_score)
      if upload: submiter.submiter().submit(entry=sfile, message=self.params)
    print("[Finished!] Elapsed time overall for %.2f secs" % (time.time() - start_time))
    return valid_score


  def get_feature_importance(self):
    mfolder = '%s/models/%s/' % (self.params['root'], self.params['stamp'])
    mpath = mfolder+'grid_model_x_0_y_0.pkl.gz'
    clf = pickle.load(gzip.open(mpath, 'rb'))

    cols = [c for c in self.params['x_cols'] if c not in ['place_id', 'row_id']]
    fimp = dict(zip(*[cols, clf.feature_importances_]))
    fimp = sorted(fimp.items(), key=lambda v: v[1], reverse=True)
    return fimp


  # skip training, evaluate from existing model
  def evaluate_model(self, evaluate=False, submit=False, upload=False):
    print("[Evaluate_model] with params=%s" % (self.params))
    start_time = time.time()
    norm = self.params.get('norm')
    df_train, df_valid, df_test = self.pas.get_data()
    valid_score = 0.0
    if evaluate:
      _, valid_score = self.eva.evaluate(df_valid, title='Test', norm=norm)
    if submit:
      preds_total, _ = self.eva.evaluate(df_test, title='Submit', norm=norm)
      sfile = self.eva.gen_submit_file(preds_total, valid_score)
      if upload: submiter.submiter().submit(entry=sfile, message=self.params)
    print("[Finished!] evaluate_model for %.2f secs" % (time.time() - start_time))


#===========================================
#   Main Flow
#===========================================
if __name__ == '__main__':
  m = main()
  m.cmd_parse(sys.argv)
  # m.run()
  m.run_cfeats()

