ALTER TABLE preprocess ADD after_norm_set varchar(500);
ALTER TABLE preprocess ADD volcano_hash MEDIUMTEXT;
ALTER TABLE preprocess ADD fold varchar(10);
ALTER TABLE preprocess ADD pvalue varchar(10);
ALTER TABLE preprocess ADD length varchar(10);
ALTER TABLE preprocess ADD fr_univariate_hash MEDIUMTEXT;
ALTER TABLE preprocess ADD classification_result_set varchar(500);