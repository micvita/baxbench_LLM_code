# EXPERIMENTAL !!!!!
# Read from all the csv analysis data if trend_sig is TRUE. If it is extract slope and confidence intervals, compute relative slope and compare with other slopes from the same metric.
# The table should have on each row a metric and then on the columns the task + env

library(readr)
library(zoo)

detect_slope_comp <- function(datain, row_index, base_partition, base_total_RAM) {

  param_name <- datain[row_index, "parameter"][[1]]

  if (grepl("Partition", param_name) && !grepl("%", param_name)) {
    curr_base <- base_partition
    perc <- 100

  } else if (grepl("Memory", param_name) && !grepl("%|Partition", param_name)) {
    curr_base <- base_total_RAM
    perc <- 100

  } else {
    curr_base <- 1
    perc <- 1
  }

  rel_slope     <- ((datain[row_index, "sen_slope"][[1]] * 60) / curr_base) * perc
  abs_slope     <- (datain[row_index, "sen_slope"][[1]] * 60)
  abs_slope_lci <- (datain[row_index, "slope_lowerCI_95"][[1]] * 60)
  abs_slope_uci <- (datain[row_index, "slope_upperCI_95"][[1]] * 60)

  return (data.frame(
    rel_slope = rel_slope,
    abs_slope = abs_slope,
    abs_slope_lci = abs_slope_lci,
    abs_slope_uci = abs_slope_uci
  ))
}

csv_files <- list.files(path = "Rscript/csv_data_analysis_results", pattern = "\\.csv$", full.names = TRUE)

base_partition <- 108240035840
base_total_RAM <- 7473991680 #bytes

dataout <- data.frame(task_env = character(), parameter = character(), slope_relative_percent_hour = numeric(), slope_abs_per_hour = numeric(),  slope_abs_per_hour_lci = numeric(),  slope_abs_per_hour_uci = numeric())

for(csv_file in csv_files) {

    datain <- read_csv(csv_file)
    height <- nrow(datain)

    task_env_name  <- paste0(datain[1, "env"][[1]], "_", datain[1, "task"][[1]])

    for(row_index in 1:height) {

        parameter_name <- datain[row_index, "parameter"][[1]]

        if(as.logical(datain[row_index, "trend_detected"][[1]])) {
            #extract slope and compute relative slope
            res <- detect_slope_comp(datain, row_index, base_partition, base_total_RAM)

            if(res$abs_slope == res$rel_slope) {
                res$rel_slope <- NA
            }

            dataout[nrow(dataout) + 1, ] <- data.frame(
                task_env = task_env_name,
                parameter = parameter_name,
                slope_relative_percent_hour = res$rel_slope,
                slope_abs_per_hour = res$abs_slope,
                slope_abs_per_hour_lci = res$abs_slope_lci,
                slope_abs_per_hour_uci = res$abs_slope_uci
            )
        }
    }
}

write_csv(dataout, file.path(getwd(), "Rscript/comparison_data", "test.csv"))